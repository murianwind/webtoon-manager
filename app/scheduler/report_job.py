"""리포트 작업 — 신규 회차/완결 요약을 모아 디스코드로 보낸다.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import aiohttp

from app import discord_notify, job_status, report_dispatch, report_seen, repository, webtoon_server_client


from app.config import get_settings


from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
from app.scheduler.report_message import build_report_message
from app.scheduler.report_sections import collect_new_episode_sections

log = logging.getLogger(__name__)

_report_job_lock = asyncio.Lock()


_SETTING_KEY_REPORT_LAST_SENT_AT = report_dispatch.LAST_SENT_KEY


_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"


_KST = ZoneInfo("Asia/Seoul")


async def run_report_job(force_test: bool = False) -> None:
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report_job_impl(force_test=force_test)


def _kst_day_range_utc(day) -> tuple[str, str]:
    """한국시간 하루(day)의 [시작, 끝)을 UTC ISO 문자열로."""
    start_kst = datetime.combine(day, datetime.min.time(), tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _select_report_rows(since: str | None, force_test: bool, until: str | None = None) -> tuple[list[dict], bool]:
    """리포트에 담을 다운로드 이력과, 어제 기록으로 대체했는지(테스트 발송에서만).

    일반 발송은 "지난 발송 이후" 이력 전부다. 테스트 발송은 그 누적 로직을 아예 안 쓴다 — 오늘(한국시간) 다운로드한 것만 보내고,
    오늘 게 없으면 어제 것만 보낸다. 전체 이력이 몰려서 나오는 걸 막기 위해 날짜 하루 단위로 딱 끊는다."""
    if not force_test:
        if not since:
            return [], False
        # until이 있으면(자동 발송) 묶음이 확정된 시각까지만 — 그 뒤에 끝난 다운로드는 다음 묶음으로 남는다
        return (repository.list_episode_history_window(since, until) if until else repository.list_episode_history_since(since)), False
    today = datetime.now(_KST).date()
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today))
    if rows:
        return rows, False
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today - timedelta(days=1)))
    return rows, bool(rows)


async def _resolve_reader_urls(session: aiohttp.ClientSession, settings, success_rows: list[dict]) -> dict[str, str]:
    """받은 작품의 웹툰 뷰어 바로가기 링크(뷰어 서버가 설정돼 있을 때만). 키: 네이버는 제목, 카카오는 "kakao:제목".
    웹툰서버는 실제 디스크 폴더명 기준으로 매칭하는데, 폴더를 만들 때는 ':' 같은 금지문자를 치환해서 저장한다 — 네이버는 전각 문자로
    (예: "제목 : 부제" → "제목 ： 부제"), 카카오는 밑줄 규칙으로. 원본 제목을 그대로 조회하면 문자가 안 맞아 실패한 사례가 있어서
    폴더명 생성과 같은 치환을 거쳐 조회한다."""
    webtoon_server_url = repository.get_setting(_SETTING_KEY_WEBTOON_SERVER_URL) or ""
    urls: dict[str, str] = {}
    if not webtoon_server_url:
        return urls
    for platform, folder_name, key_prefix in (("naver", remove_forbidden_str, ""), ("kakao", remove_forbidden_str_kakao, "kakao:")):
        for title in sorted({r["title_name"] for r in success_rows if (r.get("platform") or "naver") == platform}):
            url = await webtoon_server_client.fetch_reader_url(session, webtoon_server_url, folder_name(title), settings.request_timeout_seconds)
            if url:
                urls[f"{key_prefix}{title}"] = url
    return urls


async def _run_report_job_impl(force_test: bool = False, until: str | None = None) -> None:
    """리포트 한 통을 만들어 보낸다. until: 자동 발송에서 이번 묶음의 끝(이 시각까지의 이력만 담고, 보낸 뒤 발송 기준 시각도 이 값으로 둔다). 자동 발송은 받은 이력이 있을 때만 보내고(새 에피소드 요약은 이력과 함께만 나간다), 테스트 발송은 예전처럼 요약만 있어도 보낸다."""
    settings = get_settings()
    job_status.start("report")

    since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not since and not force_test and until is None:
        # 최초 실행이면 지금까지 쌓인 이력을 전부 몰아 보내는 대신, 지금 시점부터 집계를 시작한다 — 첫 리포트에 예전 이력이 전부
        # 딸려오는 걸 방지. 수동 테스트(force_test)일 때는 이 규칙을 건너뛴다 — 사용자가 실제 발송 형태를 확인해보고 싶은 것이므로.
        repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until or now_iso)
        job_status.log_line("report", "최초 실행 — 이번 시점부터 집계 시작 (발송 없음)")
        job_status.finish("report", success=True)
        return

    rows, used_fallback = _select_report_rows(since, force_test, until)
    if not force_test:
        rows = report_seen.filter_unseen_rows(rows)  # 이미 보고한 회차는 뺀다(같은 회차는 한 번만) — 테스트 발송은 전부 보여준다
    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    app_public_base_url = repository.get_setting("app_public_base_url") or ""

    try:
        async with aiohttp.ClientSession() as session:
            unregistered_new_episodes, kakao_new_episodes = await collect_new_episode_sections(session, settings, force_test=force_test)
            if not rows and (until is not None or (not unregistered_new_episodes and not kakao_new_episodes)):
                job_status.log_line("report", "발송할 내용 없음 (다운로드 기록도, 새 에피소드도 없음)")
                if not force_test:
                    repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until or now_iso)
                job_status.finish("report", success=True)
                return

            reader_urls = await _resolve_reader_urls(session, settings, success_rows)
            message = build_report_message(
                success_rows, failed_rows, reader_urls, unregistered_new_episodes, app_public_base_url, kakao_new_episodes
            )
            if used_fallback:
                message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
            await discord_notify.send_webhook_notification(session, message)
            if not force_test:
                # 전송이 성공한 뒤에만 기록(실패하면 다음에 다시 시도)
                report_seen.remember(unregistered_new_episodes, kakao_new_episodes)
                report_seen.remember_rows(rows)

        if not force_test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until or now_iso)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {len(success_rows)}건, 실패 {len(failed_rows)}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)
