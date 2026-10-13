"""다운로드 작업 — 네이버와 카카오페이지를 한 번에 돌리고 이력 정리와 실패 알림을 한다.
"""

import asyncio
import logging

import aiohttp

from app import cookie_health, daily_plus, discord_notify, job_status, repository, schedule_config
from app.config import Settings, get_settings

from app.scheduler.common import (
    DEFAULT_SCHEDULES,
)
from app.scheduler.kakao_download import (
    _download_kakao_subscriptions,
)
from app.scheduler.naver_download import (
    _download_new_episodes_for_one,
)

log = logging.getLogger(__name__)

# 정기 스케줄 실행과 "수동 실행" 버튼이 겹치는 걸 막는 잡별 락 (동시성 문제 방지).
_download_job_lock = asyncio.Lock()


_DOWNLOAD_TARGET_PLATFORMS = {"naver": {"naver"}, "kakao": {"kakao"}, "both": {"naver", "kakao"}}


_download_claimed_platforms: set[str] = set()  # 지금 실행 중이거나 순서를 기다리는 실행이 맡은 플랫폼


async def run_download_job(target: str = "naver") -> None:
    """target: naver | kakao | both(스케줄에서 고른 대상). 정기 스케줄과 '수동 실행' 버튼이 동시에 같은 플랫폼을
    받으려 하면 같은 웹툰을 두 실행이 동시에 다운로드할 수 있다(회차 저장/압축이 원자적이지 않음) — 이미 실행 중이거나
    기다리는 실행이 같은 플랫폼을 맡고 있으면 조용히 건너뛴다(에러 아님, "이미 실행 중"으로 로그만 남김). 서로 다른
    플랫폼(예: 네이버 스케줄과 카카오 스케줄이 같은 시각)은 건너뛰지 않고 순서를 기다렸다가 실행한다."""
    wanted = set(_DOWNLOAD_TARGET_PLATFORMS.get(target, {"naver"}))
    platforms = wanted - _download_claimed_platforms
    if not platforms:
        job_status.log_line("download", "이미 실행 중이거나 대기 중이라 건너뜁니다 (중복 실행 방지)")
        return
    if platforms != wanted:
        job_status.log_line("download", "이미 실행 중이거나 대기 중인 대상은 건너뛰고 나머지만 실행합니다 (중복 실행 방지)")
    _download_claimed_platforms.update(platforms)
    try:
        async with _download_job_lock:
            await _run_download_job_impl(platforms)
    finally:
        _download_claimed_platforms.difference_update(platforms)


async def _run_download_job_impl(platforms: set[str]) -> None:
    settings = get_settings()
    # 신작 스캔이 자동으로 구독 추가해 둔 매일+ 작품은 받지 않는다 — 저장해 둔 매일+ 목록으로(네트워크 없이) 먼저 제외됨으로 정리한다
    auto_excluded = repository.exclude_auto_added_titles(daily_plus.cached_ids()) if "naver" in platforms else []
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE) if "naver" in platforms else []

    job_status.start("download")
    if auto_excluded:
        job_status.log_line("download", f"자동 추가된 매일+ 작품 {len(auto_excluded)}개를 제외됨으로 정리했습니다: " + ", ".join(title for _, title in auto_excluded))
    if "naver" in platforms:
        job_status.log_line("download", f"다운로드 스캔 시작 — 구독 중인 네이버 웹툰 {len(active_webtoons)}개")
    else:
        job_status.log_line("download", "다운로드 스캔 시작 — 카카오페이지만")
    had_error = False

    adult_tracker = cookie_health.AdultFetchTracker()
    failures: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for webtoon in active_webtoons:
            try:
                await _download_new_episodes_for_one(session, settings, webtoon.title_id, adult_tracker, failures)
            except Exception as e:
                had_error = True
                log.error("웹툰(titleId=%s) 다운로드 처리 중 예외 — 다음 웹툰으로 진행: %s", webtoon.title_id, e)
                job_status.log_line("download", f"[{webtoon.title}] 처리 중 오류: {e}")
                failures.append({"title_name": webtoon.title, "episode_no": None, "subtitle": str(e)})
            # 새 회차가 없어서 그냥 넘어가는 웹툰들 사이에도 딜레이를 둔다 — 예전엔 이
            # 경우에만 딜레이가 전혀 없어서, 구독 웹툰이 많으면 순식간에 연속 요청이
            # 나가는 문제가 있었다(실제로 코드 확인 후 발견됨).
            await asyncio.sleep(settings.delay_seconds)

        try:
            await cookie_health.finalize_and_notify(session, settings, adult_tracker)
        except Exception as e:
            log.error("쿠키 상태 판단/알림 중 예외: %s", e)

        try:
            if "kakao" in platforms:
                await _download_kakao_subscriptions(settings, failures)
        except Exception as e:
            had_error = True
            log.error("카카오페이지 자동 다운로드 중 예외: %s", e)
            job_status.log_line("download", f"카카오페이지 자동 다운로드 중 오류: {e}")

        if failures:
            # 다운로드 리포트가 켜져 있으면 실패 목록이 리포트에도 그대로 포함되므로
            # (동일한 내용이 두 번 오는 게 실제로 불편하다는 피드백 있었음), 이 자리의
            # 즉시 실패 알림은 건너뛴다. 리포트가 꺼져 있으면(설정 안 함) 여기서라도
            # 알려야 사용자가 실패를 알 방법이 없으므로 그대로 보낸다.
            report_configured = schedule_config.get_schedule(
                "report_job", DEFAULT_SCHEDULES["report_job"]
            ).mode != "off"
            if not report_configured:
                try:
                    await _notify_download_failures(session, settings, failures)
                except Exception as e:
                    log.error("실패 요약 알림 전송 중 예외: %s", e)

    try:
        _cleanup_old_episode_history()
    except Exception as e:
        log.error("다운로드 이력 보관기간 정리 중 예외: %s", e)

    try:
        _cleanup_old_job_history()
    except Exception as e:
        log.error("실행 이력 보관기간 정리 중 예외: %s", e)

    job_status.log_line("download", "다운로드 스캔 종료")
    job_status.finish("download", success=not had_error)


def _cleanup_old_job_history() -> None:
    """설정된 보관 기간(일)이 있으면, 그보다 오래된 실행 이력(수동실행/신작스캔/다운로드
    등 잡 실행 기록)을 정리한다. 설정 안 했으면 아무것도 안 함."""
    retention_raw = repository.get_setting("job_history_retention_days")
    if not retention_raw:
        return
    retention_days = int(retention_raw)
    if retention_days <= 0:
        return
    deleted = repository.delete_job_history_older_than(retention_days)
    if deleted:
        job_status.log_line("download", f"보관기간({retention_days}일) 초과 실행 이력 {deleted}건 정리")


def _cleanup_old_episode_history() -> None:
    """설정된 보관 기간(일)이 있으면, 그보다 오래된 다운로드 이력을 정리한다
    (파일은 그대로 유지됨). 설정 안 했으면(0 또는 미설정) 아무것도 안 함."""
    retention_raw = repository.get_setting("episode_history_retention_days")
    if not retention_raw:
        return
    retention_days = int(retention_raw)
    if retention_days <= 0:
        return
    deleted = repository.delete_episode_history_older_than(retention_days)
    if deleted:
        job_status.log_line("download", f"보관기간({retention_days}일) 초과 다운로드 이력 {deleted}건 정리")


async def _notify_download_failures(
    session: aiohttp.ClientSession, settings: Settings, failures: list[dict]
) -> None:
    """이번 실행에서 실패한 웹툰/회차를 한 번에 모아서 디스코드로 보낸다 — 실패마다
    따로 알림을 보내면 스팸이 되니, 실행이 끝날 때 요약 1건으로 보낸다."""
    lines = [f"⚠️ **다운로드 실패 {len(failures)}건** (이번 실행)"]
    for f in failures:
        if f["episode_no"] is not None:
            lines.append(f"- {f['title_name']} {f['episode_no']}화 \"{f['subtitle']}\"")
        else:
            lines.append(f"- {f['title_name']}: {f['subtitle']}")
    await discord_notify.send_webhook_notification(session, settings, "\n".join(lines))
