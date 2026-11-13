"""1분마다 도는 리포트 확인(run_report_tick) — 저장된 상태(report_dispatch, viewer_scan)만 보고 움직인다.
다운로드 묶음 확정 → 뷰어 스캔 요청/대기 → 발송하지 않는 시간 대기 → 발송. 실제 한 통을 만드는 일은 report_job이 한다.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone


import aiohttp

from app import discord_config, list_alert, report_dispatch, report_seen, repository, viewer_scan


from app.config import get_settings


from app.scheduler.report_job import _SETTING_KEY_REPORT_LAST_SENT_AT, _report_job_lock, send_batch_report, send_list_alert

log = logging.getLogger(__name__)

_report_tick_lock = asyncio.Lock()


class _TickScan:
    """한 번의 확인(틱) 안에서 뷰어 스캔 조정 장치를 한 걸음만 진행하게 하는 표시 — 리포트 단계와 마지막 단계가 둘 다 부르므로 상태 조회가 겹치지 않게 한 번만 한다."""

    def __init__(self) -> None:
        self.advanced = False


async def _advance_scan(now: datetime, scan: _TickScan) -> None:
    """뷰어 스캔 조정 장치를 한 걸음 진행한다 — 한 번의 확인(틱)에서 한 번만(상태 조회가 겹치지 않게)."""
    if scan.advanced or not viewer_scan.has_work():
        return
    scan.advanced = True
    async with aiohttp.ClientSession() as session:
        await viewer_scan.tick(session, now, get_settings().request_timeout_seconds)


async def run_report_tick(now: datetime | None = None) -> None:
    """1분마다 도는 확인 — 저장된 상태(report_dispatch, viewer_scan)만 보고 움직인다(서버 요청은 스캔/발송할 때만).
    ① 다운로드 쪽: 다운로드가 끝나고 N분 동안 새 다운로드가 없으면 묶음을 확정 → 새로 받은 게 있으면 뷰어 스캔을 요청(웹훅/리포트와 상관없이) →
    보낼 이력이 있으면 스캔이 끝날 때까지 대기 → 발송하지 않는 시간이면 끝날 때까지 대기 → 발송.
    ② 아카이빙 등이 요청한 뷰어 스캔도 같은 조정 장치(viewer_scan)가 이 확인에서 진행한다."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _report_tick_lock.locked() or _report_job_lock.locked():
        return
    async with _report_tick_lock:
        scan = _TickScan()
        await _report_step(now, scan)
        await _advance_scan(now, scan)  # 리포트와 상관없이 요청된 스캔(아카이빙 등)이나 진행 중인 스캔 확인
        try:
            await _list_alert_step(now)  # 하루 한 번 도는 웹툰 전체목록 알림 — 네트워크로 오래 걸릴 수 있어서 뷰어 새로고침 뒤에 두고, 오류가 나도 이 확인을 망가뜨리지 않는다
        except Exception as e:
            log.error("웹툰 전체목록 알림 단계 오류(무시하고 계속): %s", e)


async def _list_alert_step(now: datetime) -> None:
    """웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)을 단독으로 보낼 때가 됐으면 보낸다 — 정해진 발송 시각이거나, 발송하지 않는 시간이 끝난 뒤 30분 안에 다운로드 리포트가
    안 나간 경우(그 사이 리포트가 나갔으면 거기 얹혔으므로 오늘은 이미 끝난 것이다). 하루 한 번(list_alert)."""
    if not list_alert.standalone_due(now):
        return
    async with _report_job_lock:
        await send_list_alert(now)


async def _report_step(now: datetime, scan: _TickScan) -> None:
    state = report_dispatch.load_state()
    if state["last_download_at"] is None:
        return  # 보낼 것이 대기 중이 아니다
    if state["cutoff"] is None:
        if now - datetime.fromisoformat(state["last_download_at"]) < timedelta(minutes=report_dispatch.get_batch_minutes()):
            return  # 아직 묶음이 열려 있다(마지막 다운로드 뒤 N분이 안 지남)
        state = report_dispatch.update_state(cutoff=state["last_download_at"])
    cutoff = state["cutoff"]
    since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)

    if not state["scan_requested"]:
        _request_scan_for_batch(cutoff)  # 이번 묶음에 새로 받은 게 있으면 뷰어 스캔 요청 — 리포트를 보내든 말든 뷰어는 새 파일을 훑어야 한다
        state = report_dispatch.update_state(scan_requested=True)

    rows = report_seen.filter_unseen_rows(repository.list_episode_history_window(since, cutoff)) if since else []
    if not rows or not discord_config.get_webhook_url():
        report_dispatch.finish_cycle(cutoff)  # 보낼 것이 없거나 보낼 곳이 없다 — 리포트만 건너뛴다(스캔 요청은 이미 했다)
        return
    if viewer_scan.is_ready() and not state["scan_done"]:
        await _advance_scan(now, scan)
        started = state["scan_started_at"] or now.isoformat()
        if not state["scan_started_at"]:
            state = report_dispatch.update_state(scan_started_at=started)
        waited = now - datetime.fromisoformat(started)
        if viewer_scan.covered_state(datetime.fromisoformat(cutoff)) == "waiting" and waited < timedelta(minutes=viewer_scan.get_max_wait_minutes()):
            return  # 뷰어 스캔이 끝나기를 기다린다(그 뒤에 리포트의 바로가기 링크가 새 회차를 가리킨다)
        state = report_dispatch.update_state(scan_done=True)  # 반영됐거나(done), 스캔을 못 시켰거나(failed), 너무 오래 걸려서 — 기다리지 않는다
    if report_dispatch.is_quiet(now):
        return  # 발송하지 않는 시간 — 쌓아 둔 채 끝날 때까지 기다린다
    async with _report_job_lock:
        await send_batch_report(cutoff)
    report_dispatch.finish_cycle(cutoff)


def _request_scan_for_batch(cutoff: str) -> None:
    """확정된 묶음(이전 확인 시점 ~ cutoff)에 새로 받은 회차(성공)가 있으면 뷰어 스캔을 요청한다. 같은 이력을 다시 따지지 않게 워터마크를 옮긴다."""
    since = viewer_scan.load_state()["watermark"] or repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
    cutoff_at = datetime.fromisoformat(cutoff)
    downloaded = sum(1 for row in repository.list_episode_history_window(since, cutoff) if row["status"] == "success") if since else 0
    if downloaded:
        viewer_scan.request(cutoff_at, reason=f"다운로드 {downloaded}건 완료")
    viewer_scan.set_watermark(cutoff_at)
