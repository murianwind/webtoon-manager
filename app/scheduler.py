"""
주기 작업 정의 + 진행상황 로깅 + 잡별 스케줄(끄기/N분마다/특정 요일·시각) 관리.

두 가지 독립적인 작업으로 나눈다:
  - discovery_job   : 완결 감지(+감지 즉시 디스코드 봇으로 확인 메시지 전송) + 작가/태그 신작 자동추가
  - download_job    : 구독 중인 웹툰의 새 회차 다운로드 (회차 하나마다 압축까지 끝내고 다음 화로 진행)

(예전에는 디스코드 완결-확인 스레드를 폴링하는 commands_job이 따로 있었지만,
discord_bot.py의 실시간 Gateway 봇으로 대체되어 더 이상 필요 없다 — 폴링 자체가 없어짐.)

각 잡은 웹툰 하나 처리 중 예외가 나도 다른 웹툰 처리를 막지 않도록 individually try/except.
각 잡의 스케줄은 DB(settings 테이블)에 사용자가 저장한 값이 있으면 그걸 쓰고, 없으면
기본값(신작 스캔 6시간마다 / 다운로드 1시간마다, 전부 interval 모드)을 쓴다 —
설정 페이지에서 바꾸면 reschedule_all()로 즉시 반영된다.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import archiver, comicinfo, cookie_health, discord_bot, discord_notify, job_status, naver_api, repository, schedule_config, tracker, webtoon_server_client
from app import rclone_updater
from app.config import Settings, get_settings
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import find_last_downloaded_episode_no
from app.schedule_config import JobSchedule
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)

DEFAULT_SCHEDULES: dict[str, JobSchedule] = {
    "discovery_job": JobSchedule(mode="interval", interval_minutes=360),
    "download_job": JobSchedule(mode="interval", interval_minutes=60),
    "report_job": JobSchedule(mode="off"),  # 사용자가 원하는 시각으로 직접 설정해야 켜짐
    "archive_job": JobSchedule(mode="off"),
}

# 정기 스케줄 실행과 "수동 실행" 버튼이 겹치는 걸 막는 잡별 락 (동시성 문제 방지).
_download_job_lock = asyncio.Lock()
_discovery_job_lock = asyncio.Lock()
_report_job_lock = asyncio.Lock()
_archive_job_lock = asyncio.Lock()


async def _download_new_episodes_for_one(
    session: aiohttp.ClientSession,
    settings: Settings,
    title_id: str,
    adult_tracker: cookie_health.AdultFetchTracker,
    failures: list[dict],
) -> None:
    webtoon = repository.get(title_id)
    if webtoon is None or webtoon.status != repository.STATUS_ACTIVE:
        return

    info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
    if info is None:
        job_status.log_line("download", f"[{webtoon.title}] 정보 조회 실패, 건너뜀")
        return

    repository.update_is_adult(title_id, info.is_adult)

    cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
    if info.is_adult and not cookies:
        job_status.log_line("download", f"[{info.title_name}] 성인 웹툰 인증 쿠키 없음, 건너뜀")
        return
    cookies = cookies or {}

    safe_title = remove_forbidden_str(info.title_name)
    webtoon_dir = Path(settings.download_root) / safe_title

    all_episodes = await naver_api.fetch_all_episodes(
        session, title_id, cookies, settings.request_timeout_seconds
    )
    if info.is_adult:
        # 쿠키 만료 감지용 — 별도 API 호출 없이, 이번에 실제로 받아온 회차 개수를 그대로 신호로 쓴다.
        adult_tracker.record(len(all_episodes))

    free_episodes = naver_api.free_episodes_only(all_episodes)

    if free_episodes:
        repository.update_latest_episode_no(title_id, free_episodes[-1].episode_no)

    # DB에 저장된 last_downloaded_no만 믿지 않고, 매번 실제 폴더의 마지막 zip 파일명을
    # 부제목 기준으로 네이버 회차 목록과 대조해서 확인한다. 네이버 회차 번호(no)는
    # 가끔 건너뛰기 때문에(예: 109 다음이 111), 로컬 zip 개수를 세서 위치로 추론하면
    # 어긋난다 — 그래서 반드시 부제목 텍스트로 실제 회차를 찾아야 한다.
    last_no = webtoon.last_downloaded_no
    folder_last_no = find_last_downloaded_episode_no(webtoon_dir, free_episodes)
    if folder_last_no > last_no:
        job_status.log_line(
            "download",
            f"[{info.title_name}] 폴더 확인 결과 {folder_last_no}화까지 완료 (DB 기록 {last_no}화에서 갱신)",
        )
        last_no = folder_last_no
        repository.update_last_downloaded_no(title_id, last_no)

    if comicinfo.needs_comicinfo(webtoon_dir):
        webtoon_dir.mkdir(parents=True, exist_ok=True)
        comicinfo.write_comicinfo_file(webtoon_dir, info)
        await comicinfo.download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
        job_status.log_line("download", f"[{info.title_name}] ComicInfo.xml / 커버 이미지 생성")

    pending = [ep for ep in free_episodes if ep.episode_no > last_no]
    if not pending:
        return

    total_pending = len(pending)
    job_status.log_line("download", f"[{info.title_name}] 새 회차 {total_pending}개 다운로드 시작")

    # 상한에 걸리면 "다음 정기 실행까지 대기"가 아니라, 이번 실행 안에서 batch_rest_minutes만큼
    # 쉬었다가 이어서 계속 받는다 — 하루 한 번처럼 뜸하게 도는 스케줄에서는 다음 정기 실행까지
    # 기다리면 너무 오래 걸리기 때문. 실패하면(회로차단) 그 즉시 전부 멈추고 다음 실행에서 재시도한다.
    cap = settings.max_new_episodes_per_title
    while pending:
        batch = pending[:cap] if cap > 0 else pending
        pending = pending[len(batch):]

        for episode in batch:
            success, _episode_dir = await download_single_episode(
                session=session,
                title_id=title_id,
                title_name=info.title_name,
                webtoon_type=info.webtoon_type,
                episode=episode,
                cookies=cookies,
                download_root=settings.download_root,
                folder_zero_fill=settings.folder_zero_fill,
                image_zero_fill=settings.image_zero_fill,
                max_concurrent_downloads=settings.max_concurrent_downloads,
                timeout_seconds=settings.request_timeout_seconds,
            )

            if not success:
                job_status.log_line(
                    "download", f"[{info.title_name}] {episode.episode_no}화 다운로드 실패 — 다음 실행에서 재시도"
                )
                repository.add_episode_history(
                    title_id, info.title_name, episode.episode_no, episode.subtitle, "failed", "이미지 URL 수집 또는 다운로드 오류"
                )
                failures.append(
                    {"title_name": info.title_name, "episode_no": episode.episode_no, "subtitle": episode.subtitle}
                )
                return  # 이 작품은 여기서 완전히 중단 — 배치 남았어도 더 안 받음

            # 다운로드 → 압축 → 폴더 삭제 → (다음 루프에서) 다음 화, 순서로 진행한다.
            zip_episode_folders(webtoon_dir)
            repository.update_last_downloaded_no(title_id, episode.episode_no)
            repository.add_episode_history(title_id, info.title_name, episode.episode_no, episode.subtitle, "success")
            job_status.log_line("download", f"[{info.title_name}] {episode.episode_no}화 완료 (압축 후 폴더 삭제)")
            await asyncio.sleep(settings.delay_seconds)

        if pending:
            rest_minutes = settings.batch_rest_minutes
            job_status.log_line(
                "download",
                f"[{info.title_name}] {cap}화 받음, 남은 {len(pending)}화는 {rest_minutes}분 쉬었다가 이어받기",
            )
            await asyncio.sleep(rest_minutes * 60)


async def run_download_job() -> None:
    """정기 스케줄과 '수동 실행' 버튼이 동시에 이 잡을 실행하면, 같은 웹툰을 두 실행이
    동시에 다운로드하려고 시도할 수 있다(회차 저장/압축이 원자적이지 않음) — 잡별
    락으로 겹치는 실행은 조용히 건너뛴다(에러 아님, 그냥 "이미 실행 중"으로 로그만 남김)."""
    if _download_job_lock.locked():
        job_status.log_line("download", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _download_job_lock:
        await _run_download_job_impl()


async def _run_download_job_impl() -> None:
    settings = get_settings()
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE)

    job_status.start("download")
    job_status.log_line("download", f"다운로드 스캔 시작 — 구독 중인 웹툰 {len(active_webtoons)}개")
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


_SETTING_KEY_REPORT_LAST_SENT_AT = "report_last_sent_at"
_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"
_REPORT_LIST_LIMIT = 40  # 디스코드 메시지 길이 제한 대비, 항목이 너무 많으면 일부만 나열


def _build_report_message(success_rows: list[dict], failed_rows: list[dict], reader_urls: dict[str, str]) -> str:
    """예전 hermes webtoon_checker.py의 메시지 구조(다운로드됨/실패)를 그대로 따른다."""
    today_label = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    success_titles = sorted({r["title_name"] for r in success_rows})
    success_lines = []
    for title in success_titles:
        url = reader_urls.get(title)
        success_lines.append(f"• {title} [바로가기]({url})" if url else f"• {title}")

    failed_titles = sorted({r["title_name"] for r in failed_rows})

    parts = [
        f"📅 웹툰 다운로드 리포트 ({today_label})",
        "",
        f"📁 받은 작품 ({len(success_titles)}):",
        "\n".join(success_lines[:_REPORT_LIST_LIMIT]) if success_lines else "없음",
    ]
    if len(success_lines) > _REPORT_LIST_LIMIT:
        parts.append(f"_외 {len(success_lines) - _REPORT_LIST_LIMIT}개 생략_")

    if failed_titles:
        parts.extend([
            "",
            f"❌ 실패한 작품 ({len(failed_titles)}):",
            "\n".join(failed_titles[:_REPORT_LIST_LIMIT]),
        ])
        if len(failed_titles) > _REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(failed_titles) - _REPORT_LIST_LIMIT}개 생략_")

    return "\n".join(parts)


async def run_report_job(force_test: bool = False) -> None:
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report_job_impl(force_test=force_test)


async def run_archive_job() -> None:
    if _archive_job_lock.locked():
        job_status.log_line("archive", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _archive_job_lock:
        settings = get_settings()
        job_status.start("archive")
        try:
            update_result = await rclone_updater.check_and_update()
            job_status.log_line("archive", update_result)
        except Exception as e:
            log.error("rclone 자동 업데이트 확인 중 예외: %s", e)
            job_status.log_line("archive", f"rclone 업데이트 확인 중 오류(무시하고 계속): {e}")
        try:
            moved = await asyncio.to_thread(archiver.run_periodic_archive, settings.archive_root, settings.download_root, settings.rclone_config_path)
            job_status.log_line("archive", f"지정 웹툰 {moved}개 파일 이동 완료")

            pending_moved = await asyncio.to_thread(
                archiver.process_pending_finish_archives, settings.archive_root, settings.download_root, settings.rclone_config_path
            )
            job_status.log_line("archive", f"완결 구독해제 대기열 {pending_moved}개 파일 이동 완료")

            job_status.finish("archive", success=True)
        except Exception as e:
            log.error("아카이빙 잡 중 예외: %s", e)
            job_status.log_line("archive", f"오류: {e}")
            job_status.finish("archive", success=False)


async def _run_report_job_impl(force_test: bool = False) -> None:
    settings = get_settings()
    job_status.start("report")

    since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not since and not force_test:
        # 최초 실행이면 지금까지 쌓인 이력을 전부 몰아 보내는 대신, 지금 시점부터
        # 집계를 시작한다 — 첫 리포트에 예전 이력이 전부 딸려오는 걸 방지.
        # 수동 테스트(force_test)일 때는 이 규칙을 건너뛴다 — 사용자가 실제로 발송
        # 형태를 확인해보고 싶은 것이므로.
        repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", "최초 실행 — 이번 시점부터 집계 시작 (발송 없음)")
        job_status.finish("report", success=True)
        return

    if force_test:
        # 테스트 발송은 "지난 발송 이후" 누적 로직을 아예 안 쓴다 — 오늘(한국시간)
        # 다운로드한 것만 보내고, 오늘 게 없으면 어제 것만 보낸다. 단순하고 예측
        # 가능하게: 전체 이력이 몰려서 나오는 걸 막기 위한 것이라 날짜 하루 단위로
        # 딱 끊는다.
        kst = ZoneInfo("Asia/Seoul")
        today_kst = datetime.now(kst).date()
        used_fallback = False

        def _kst_day_range_utc(day):
            start_kst = datetime.combine(day, datetime.min.time(), tzinfo=kst)
            end_kst = start_kst + timedelta(days=1)
            return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()

        start_iso, end_iso = _kst_day_range_utc(today_kst)
        rows = repository.list_episode_history_between(start_iso, end_iso)
        if not rows:
            start_iso, end_iso = _kst_day_range_utc(today_kst - timedelta(days=1))
            rows = repository.list_episode_history_between(start_iso, end_iso)
            used_fallback = bool(rows)
    else:
        rows = repository.list_episode_history_since(since) if since else []
        used_fallback = False

    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]

    if not rows:
        job_status.log_line("report", "발송할 내용 없음 (다운로드 기록 자체가 없음)")
        if not force_test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.finish("report", success=True)
        return

    webtoon_server_url = repository.get_setting(_SETTING_KEY_WEBTOON_SERVER_URL) or ""
    reader_urls: dict[str, str] = {}

    try:
        async with aiohttp.ClientSession() as session:
            if webtoon_server_url:
                for title in sorted({r["title_name"] for r in success_rows}):
                    # 웹툰서버는 실제 디스크 폴더명 기준으로 매칭한다. 그런데 폴더를
                    # 만들 때는 ':' 같은 금지문자를 전각 문자로 치환해서 저장하는데
                    # (예: "제목 : 부제" → "제목 ： 부제"), 여기선 원본 제목(치환 전)을
                    # 그대로 조회에 쓰고 있어서 문자가 안 맞아 조회가 실패하는 경우가
                    # 있었다(실제로 확인된 사례) — 폴더명 생성과 동일한 치환을 거쳐서 조회한다.
                    folder_safe_title = remove_forbidden_str(title)
                    url = await webtoon_server_client.fetch_reader_url(
                        session, webtoon_server_url, folder_safe_title, settings.request_timeout_seconds
                    )
                    if url:
                        reader_urls[title] = url

            message = _build_report_message(success_rows, failed_rows, reader_urls)
            if used_fallback:
                message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
            await discord_notify.send_webhook_notification(session, settings, message)

        if not force_test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {len(success_rows)}건, 실패 {len(failed_rows)}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)


async def _notify_newly_finished() -> None:
    """완결 감지됐는데 아직 디스코드로 알리지 않은 웹툰에 실시간 봇으로 확인 메시지를 보낸다."""
    to_notify = [
        wt
        for wt in repository.list_by_status(repository.STATUS_ACTIVE)
        if wt.is_finished and not wt.finish_notified and not wt.finish_ack
    ]
    if not to_notify:
        return

    sent = 0
    for wt in to_notify:
        try:
            await discord_bot.send_completion_prompt(wt.title_id, wt.title)
            repository.set_finish_notified(wt.title_id)
            sent += 1
        except Exception as e:
            log.error("완결 알림 전송 중 예외 (titleId=%s): %s", wt.title_id, e)

    job_status.log_line("discovery", f"완결 확인 알림 {sent}건 전송")


async def run_discovery_job() -> None:
    """download_job과 동일한 이유로 잡별 락을 건다 — 겹치는 실행은 건너뛴다."""
    if _discovery_job_lock.locked():
        job_status.log_line("discovery", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _discovery_job_lock:
        await _run_discovery_job_impl()


async def _run_discovery_job_impl() -> None:
    settings = get_settings()
    job_status.start("discovery")
    had_error = False

    async with aiohttp.ClientSession() as session:
        try:
            job_status.log_line("discovery", "썸네일 없는 웹툰 채우는 중")
            filled = await tracker.backfill_missing_thumbnails(session, settings)
            job_status.log_line("discovery", f"썸네일 {filled}개 채움")
        except Exception as e:
            had_error = True
            log.error("썸네일 백필 중 예외: %s", e)
            job_status.log_line("discovery", f"썸네일 백필 오류: {e}")

        try:
            job_status.log_line("discovery", "작가 기반 신작 스캔 시작")
            await tracker.scan_subscriptions_for_updates(session, settings)
            job_status.log_line("discovery", "작가 기반 신작 스캔 완료")
        except Exception as e:
            had_error = True
            log.error("작가 기반 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"작가 기반 신작 스캔 오류: {e}")

        try:
            job_status.log_line("discovery", "태그 기반 신작 스캔 시작")
            await tracker.scan_curation_tags(session, settings)
            job_status.log_line("discovery", "태그 기반 신작 스캔 완료")
        except Exception as e:
            had_error = True
            log.error("태그 기반 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"태그 기반 신작 스캔 오류: {e}")

        try:
            job_status.log_line("discovery", "구독해제/제외됨 정보 갱신 시작")
            refreshed = await tracker.refresh_inactive_metadata(session, settings)
            job_status.log_line("discovery", f"구독해제/제외됨 {refreshed}개 정보 갱신 완료")
        except Exception as e:
            had_error = True
            log.error("구독해제/제외됨 정보 갱신 중 예외: %s", e)
            job_status.log_line("discovery", f"구독해제/제외됨 정보 갱신 오류: {e}")

        try:
            job_status.log_line("discovery", "카카오웹툰 작가 신작 스캔 시작")
            kakao_new_count = await tracker.scan_kakao_authors_for_new_titles(session, settings)
            job_status.log_line("discovery", f"카카오웹툰 신작 {kakao_new_count}건 발견")
        except Exception as e:
            had_error = True
            log.error("카카오웹툰 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"카카오웹툰 신작 스캔 오류: {e}")

    try:
        await _notify_newly_finished()
    except Exception as e:
        had_error = True
        log.error("완결 알림 처리 중 예외: %s", e)

    job_status.finish("discovery", success=not had_error)


_JOB_FUNCS = {
    "discovery_job": run_discovery_job,
    "download_job": run_download_job,
    "report_job": run_report_job,
    "archive_job": run_archive_job,
}


def _build_trigger(schedule: JobSchedule):
    """off면 None(=스케줄 없음), interval이면 N분마다, cron이면 지정한 시:분들/요일에.
    hour/minute은 한국시간 기준으로 해석된다(스케줄러 자체가 Asia/Seoul로 고정됨).
    시각을 여러 개 지정할 수 있어서(예: 07:00과 19:30 둘 다), 시각마다 CronTrigger를
    하나씩 만들고 OrTrigger로 묶는다 — hour/minute을 각각 콤마로 나열하면(예:
    hour='7,19', minute='0,30') 두 필드가 독립적으로 평가되어 07:30/19:00처럼
    의도하지 않은 조합까지 걸리므로, 반드시 각 시각을 별개 트리거로 만들어야 한다."""
    if schedule.mode == "off":
        return None
    if schedule.mode == "cron":
        day_of_week = ",".join(schedule.cron_days) if schedule.cron_days else "*"
        triggers = [
            CronTrigger(hour=t["hour"], minute=t["minute"], day_of_week=day_of_week, timezone="Asia/Seoul")
            for t in schedule.cron_times
        ]
        if len(triggers) == 1:
            return triggers[0]
        return OrTrigger(triggers)
    return IntervalTrigger(minutes=schedule.interval_minutes)


def _apply_job_schedule(scheduler: AsyncIOScheduler, job_id: str) -> None:
    schedule = schedule_config.get_schedule(job_id, DEFAULT_SCHEDULES[job_id])
    trigger = _build_trigger(schedule)
    existing = scheduler.get_job(job_id)

    if trigger is None:
        if existing:
            scheduler.remove_job(job_id)
        return

    if existing:
        scheduler.reschedule_job(job_id, trigger=trigger)
    else:
        scheduler.add_job(_JOB_FUNCS[job_id], trigger=trigger, id=job_id)


def create_scheduler() -> AsyncIOScheduler:
    # 컨테이너의 시스템 시간대(보통 UTC)와 무관하게 항상 한국시간으로 해석하도록
    # 명시한다 — 명시 안 하면 APScheduler가 컨테이너의 시스템 기본값(UTC)을 쓰는데,
    # 설정 화면에서 "07:00"이라고 넣으면 사용자는 한국시간 07:00을 기대하지만
    # 실제로는 UTC 07:00(한국시간 오후 4시)에 실행되고 있었다 — 실제로 확인된 버그.
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
    return scheduler


def reschedule_all(scheduler: AsyncIOScheduler) -> None:
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
