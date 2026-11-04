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


async def run_test_report() -> None:
    """리포트 발송 버튼(테스트 발송) — 오늘(없으면 어제) 기록과 새 에피소드 요약을 보낸다. 이미 알린 회차도 보여 주고, 발송 기준 시각과 "알렸다"는 기록은 건드리지 않는다."""
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report(test=True)


async def send_batch_report(until: str) -> None:
    """자동 발송 — 1분 확인(report_tick)이 묶음을 확정하고 부른다(그쪽에서 _report_job_lock을 잡고 있다). until: 이번 묶음의 끝 — 지난 발송 기준 시각부터 이 시각까지의 이력만 담고,
    보낸 뒤(또는 보낼 것이 없을 때) 발송 기준 시각도 이 값으로 둔다. 받은 이력이 있을 때만 보내고(새 에피소드 요약은 이력과 함께만 나간다), 같은 회차는 한 번만 알린다."""
    await _run_report(test=False, until=until)


def _kst_day_range_utc(day) -> tuple[str, str]:
    """한국시간 하루(day)의 [시작, 끝)을 UTC ISO 문자열로."""
    start_kst = datetime.combine(day, datetime.min.time(), tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _select_report_rows(test: bool, until: str | None) -> tuple[list[dict], bool]:
    """리포트에 담을 다운로드 이력과, 어제 기록으로 대체했는지(테스트 발송에서만).

    자동 발송은 "지난 발송 기준 시각 ~ 묶음의 끝(until)"의 이력 중 아직 알리지 않은 회차다(기준 시각이 없으면 담을 것이 없다 — 옛 이력이 몰려 나가지 않게).
    테스트 발송은 그 누적 로직을 아예 안 쓴다 — 오늘(한국시간) 다운로드한 것만 보내고, 오늘 게 없으면 어제 것만 보낸다. 전체 이력이 몰려서 나오는 걸 막기 위해 날짜 하루 단위로 딱 끊는다."""
    if not test:
        since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
        if not since:
            return [], False
        return report_seen.filter_unseen_rows(repository.list_episode_history_window(since, until)), False
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


async def _send_message(session: aiohttp.ClientSession, settings, rows: list[dict], unregistered_new_episodes: list, kakao_new_episodes: list, used_fallback: bool) -> None:
    """리포트 메시지를 만들어(받은 작품의 뷰어 링크 포함) 디스코드로 보낸다. 실패하면 예외가 그대로 올라간다."""
    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    reader_urls = await _resolve_reader_urls(session, settings, success_rows)
    message = build_report_message(
        success_rows, failed_rows, reader_urls, unregistered_new_episodes, repository.get_setting("app_public_base_url") or "", kakao_new_episodes
    )
    if used_fallback:
        message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
    await discord_notify.send_webhook_notification(session, message)


async def _run_report(*, test: bool, until: str | None = None) -> None:
    """리포트 한 통을 만들어 보낸다(test: 테스트 발송, 아니면 until까지의 묶음 발송)."""
    settings = get_settings()
    job_status.start("report")
    rows, used_fallback = _select_report_rows(test, until)
    try:
        async with aiohttp.ClientSession() as session:
            unregistered_new_episodes, kakao_new_episodes = await collect_new_episode_sections(session, settings, force_test=test)
            # 자동 발송은 받은 이력이 있을 때만 보낸다(새 에피소드 요약은 이력과 함께만). 테스트 발송은 요약만 있어도 보낸다.
            if not rows and (not test or (not unregistered_new_episodes and not kakao_new_episodes)):
                job_status.log_line("report", "발송할 내용 없음 (다운로드 기록도, 새 에피소드도 없음)")
                if not test:
                    repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until)
                job_status.finish("report", success=True)
                return

            await _send_message(session, settings, rows, unregistered_new_episodes, kakao_new_episodes, used_fallback)
            if not test:
                # 전송이 성공한 뒤에만 기록(실패하면 다음에 다시 시도)
                report_seen.remember(unregistered_new_episodes, kakao_new_episodes)
                report_seen.remember_rows(rows)

        if not test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {sum(1 for r in rows if r['status'] == 'success')}건, 실패 {sum(1 for r in rows if r['status'] == 'failed')}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)
