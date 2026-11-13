"""리포트 작업 — 신규 회차/완결 요약을 모아 디스코드로 보낸다.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import aiohttp

from app import discord_notify, job_status, list_alert, report_dispatch, report_seen, repository, webtoon_server_client


from app.config import get_settings


from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
from app.scheduler.report_message import build_list_alert_message, build_report_message
from app.scheduler.report_sections import collect_new_episode_sections, collect_new_title_section

log = logging.getLogger(__name__)

_report_job_lock = asyncio.Lock()


_SETTING_KEY_REPORT_LAST_SENT_AT = report_dispatch.LAST_SENT_KEY


_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"
_MAX_LINK_LINES = 10  # 실행 이력에 풀어서 남기는 "링크를 못 찾은 작품" 수


_KST = ZoneInfo("Asia/Seoul")


async def run_test_report() -> None:
    """리포트 발송 버튼(테스트 발송) — 오늘(없으면 어제) 기록과 새 에피소드 요약을 보낸다. 이미 알린 회차도 보여 주고, 발송 기준 시각과 "알렸다"는 기록은 건드리지 않는다."""
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report(test=True)


async def send_batch_report(until: str, now: datetime | None = None) -> None:
    """자동 발송 — 1분 확인(report_tick)이 묶음을 확정하고 부른다(그쪽에서 _report_job_lock을 잡고 있다). until: 이번 묶음의 끝 — 지난 발송 기준 시각부터 이 시각까지의 이력만 담고,
    보낸 뒤(또는 보낼 것이 없을 때) 발송 기준 시각도 이 값으로 둔다. 받은 이력이 있을 때만 보낸다. 웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)은 하루 한 번,
    list_alert.should_attach가 허락하는 리포트에만 얹힌다(발송하지 않는 시간이 끝난 뒤 처음 나가는 리포트). now: 지금 시각(테스트에서 바꿀 수 있게)."""
    await _run_report(test=False, until=until, now=now)


async def send_list_alert(now: datetime | None = None) -> None:
    """웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)만 따로 보낸다 — 정해진 발송 시각이나, 발송하지 않는 시간이 끝난 뒤 30분 안에 다운로드 리포트가 안 나갔을 때(report_tick이 부른다,
    그쪽에서 _report_job_lock을 잡고 있다). 보낼 것이 없으면 아무것도 안 보내고 그날은 확인한 것으로 친다. 전송이 실패하면 그날 안에 다시 시도한다(list_alert.RETRY_MINUTES 뒤)."""
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    job_status.start("report")
    try:
        async with aiohttp.ClientSession() as session:
            naver_new, kakao_new = await collect_new_title_section(session, settings, force_test=False)
            naver_eps, kakao_eps = await collect_new_episode_sections(session, settings, force_test=False)
            message = build_list_alert_message(naver_new, kakao_new, naver_eps, kakao_eps, repository.get_setting("app_public_base_url") or "")
            if not message:
                job_status.log_line("report", "웹툰 전체목록 알림: 알릴 신규 웹툰/새 에피소드가 없습니다")
            else:
                await discord_notify.send_webhook_notification(session, message)
                report_seen.remember(naver_eps, kakao_eps)
                report_seen.remember_new(naver_new, kakao_new)
                job_status.log_line("report", f"웹툰 전체목록 알림 발송 완료 (신규 웹툰 {len(naver_new) + len(kakao_new)}개, 새 에피소드 {len(naver_eps) + len(kakao_eps)}개)")
        list_alert.mark_done(now)
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("웹툰 전체목록 알림 발송 중 예외: %s", e)
        list_alert.note_failure(now)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)


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
    missing: list[str] = []  # 링크를 못 찾은 작품(표시 이름)
    looked_up = 0
    for platform, folder_name, key_prefix in (("naver", remove_forbidden_str, ""), ("kakao", remove_forbidden_str_kakao, "kakao:")):
        for title in sorted({r["title_name"] for r in success_rows if (r.get("platform") or "naver") == platform}):
            looked_up += 1
            url = await webtoon_server_client.fetch_reader_url(session, webtoon_server_url, folder_name(title), settings.request_timeout_seconds)
            if url:
                urls[f"{key_prefix}{title}"] = url
            else:
                missing.append(f"[카카오] {title}" if platform == "kakao" else title)
    if looked_up:  # 실행 이력("웹툰 서버 연동")에 이번 리포트의 링크 조회 결과를 한 건으로 남긴다
        lines = [f"다운로드 리포트 — 웹툰 서버에서 바로가기 링크를 조회했습니다 ({looked_up}개 작품: 성공 {looked_up - len(missing)}, 실패 {len(missing)})"]
        lines += [f"- {name}: 링크를 찾지 못함" for name in missing[:_MAX_LINK_LINES]]
        if len(missing) > _MAX_LINK_LINES:
            lines.append(f"… 외 {len(missing) - _MAX_LINK_LINES}개 작품")
        job_status.record_event("viewer_server", lines, success=not missing)
    return urls


async def _send_message(
    session: aiohttp.ClientSession, settings, rows: list[dict], unregistered_new_episodes: list, kakao_new_episodes: list, used_fallback: bool,
    new_titles: list, kakao_new_titles: list,
) -> None:
    """리포트 메시지를 만들어(받은 작품의 뷰어 링크 포함) 디스코드로 보낸다. 실패하면 예외가 그대로 올라간다."""
    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    reader_urls = await _resolve_reader_urls(session, settings, success_rows)
    message = build_report_message(
        success_rows, failed_rows, reader_urls, unregistered_new_episodes, repository.get_setting("app_public_base_url") or "", kakao_new_episodes,
        new_titles=new_titles, kakao_new_titles=kakao_new_titles,
    )
    if used_fallback:
        message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
    await discord_notify.send_webhook_notification(session, message)


async def _run_report(*, test: bool, until: str | None = None, now: datetime | None = None) -> None:
    """리포트 한 통을 만들어 보낸다(test: 테스트 발송, 아니면 until까지의 묶음 발송)."""
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    job_status.start("report")
    rows, used_fallback = _select_report_rows(test, until)
    attach_list_alert = test or list_alert.should_attach(now)  # 웹툰 전체목록 알림은 테스트 발송이거나 오늘 얹을 차례인 리포트에만

    try:
        async with aiohttp.ClientSession() as session:
            naver_new, kakao_new, unregistered_new_episodes, kakao_new_episodes = [], [], [], []
            if attach_list_alert:
                naver_new, kakao_new = await collect_new_title_section(session, settings, force_test=test)
                unregistered_new_episodes, kakao_new_episodes = await collect_new_episode_sections(session, settings, force_test=test)
            # 자동 발송은 받은 이력이 있을 때만 보낸다(웹툰 전체목록 알림은 이력과 함께만). 테스트 발송은 알림만 있어도 보낸다.
            has_list_alert = bool(naver_new or kakao_new or unregistered_new_episodes or kakao_new_episodes)
            if not rows and (not test or not has_list_alert):
                job_status.log_line("report", "발송할 내용 없음 (다운로드 기록도, 새 에피소드도 없음)")
                if not test:
                    repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until)
                job_status.finish("report", success=True)
                return

            await _send_message(session, settings, rows, unregistered_new_episodes, kakao_new_episodes, used_fallback, naver_new, kakao_new)
            if not test:
                # 전송이 성공한 뒤에만 기록(실패하면 다음에 다시 시도)
                report_seen.remember(unregistered_new_episodes, kakao_new_episodes)
                report_seen.remember_new(naver_new, kakao_new)
                report_seen.remember_rows(rows)
                if attach_list_alert:
                    list_alert.mark_done(now)  # 얹을 차례였으면(알릴 것이 없었어도) 오늘은 확인한 것

        if not test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, until)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {sum(1 for r in rows if r['status'] == 'success')}건, 실패 {sum(1 for r in rows if r['status'] == 'failed')}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)
