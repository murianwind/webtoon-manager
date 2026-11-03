"""빠른 상태 스캔 작업 — 수동 실행 버튼, 전체목록 새로고침(범위 지정), 매일 새벽 자동 실행이 같이 쓴다(스캔 자체는 app/quick_scan.py).

대상 고르기 → 스캔 → 작업 로그(실행 이력)에 결과를 남긴다. 예전에 화면(기타 탭)에 띄우던 두 문구 — "기타 원인 …"과 "완결 웹툰 N개는 제외됨 목록에 표시하지 않습니다" — 도
화면 대신 이 로그의 마지막 줄들로 남긴다. 겹치는 실행은 건너뛴다.
"""
import asyncio
import logging

from app import job_status, quick_scan, weekday_diagnostics
from app.config import get_settings

log = logging.getLogger(__name__)

MANUAL_MAX_AGE_HOURS = 1  # 수동 실행/새로고침: 최근 1시간 안에 확인한 작품은 건너뛴다(눌렀는데 아무것도 안 하는 일이 없게 짧게)
NIGHTLY_MAX_AGE_HOURS = 12  # 매일 새벽 자동 실행: 12시간 안에 확인한 작품은 건너뛴다
_DAY_LABELS = {"etc": "기타", "mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}

_quick_scan_lock = asyncio.Lock()


def _say(line: str) -> None:
    job_status.log_line("quick_scan", line)


async def run_quick_scan_job(scope: set[str] | None = None, max_age_hours: float = MANUAL_MAX_AGE_HOURS) -> None:
    """scope: 확인할 그룹(etc/mon~sun) — 없으면 전부(기타 먼저, 오늘부터 요일별로)."""
    if _quick_scan_lock.locked():
        _say("이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _quick_scan_lock:
        job_status.start("quick_scan")
        try:
            today = quick_scan.today_key()
            targets, skipped = await asyncio.to_thread(quick_scan.collect_targets, today=today, max_age_hours=max_age_hours, scope=scope)
            _announce_start(targets, skipped, today, scope, max_age_hours)
            if targets:
                stats = await quick_scan.scan(targets, get_settings(), progress=_say)
                _say(f"빠른 상태 스캔 완료 — 확인 {stats.scanned}개: 요일 새로 알아냄 {stats.resolved_days}개, 새로 완결·서비스 종료·판매 중지 {stats.newly_finished}개, 확인 실패 {stats.failed}개")
            await asyncio.to_thread(_log_summary_lines)
            job_status.finish("quick_scan", success=True)
        except Exception as e:
            log.exception("빠른 상태 스캔 중 예외")
            _say(f"오류: {e}")
            job_status.finish("quick_scan", success=False)


def _announce_start(targets: list[quick_scan.Target], skipped: int, today: str, scope: set[str] | None, max_age_hours: float) -> None:
    if not targets:
        _say("확인할 작품이 없습니다")
    else:
        naver = sum(1 for t in targets if t.platform == "naver")
        etc = sum(1 for t in targets if t.group == "etc")
        order = f"요일을 모르는 기타 {etc}개를 먼저, 이어서 오늘({_DAY_LABELS[today]})부터 요일별로 확인합니다"
        if scope is not None:
            order = "범위: " + ", ".join(_DAY_LABELS[g] for g in quick_scan.group_order(today) if g in scope)
        _say(f"빠른 상태 스캔 시작 — 대상 {len(targets)}개 (네이버 {naver}개, 카카오 {len(targets) - naver}개): {order}")
    if skipped:
        _say(f"최근 {max_age_hours:g}시간 안에 확인한 {skipped}개는 건너뜁니다")


def _log_summary_lines() -> None:
    """화면에 띄우던 문구를 이력으로 — 기타에 남은 작품의 원인, 제외됨에서 가려지는 작품 수."""
    gaps = weekday_diagnostics.describe(weekday_diagnostics.summarize(None))
    _say(gaps or "기타에 남은 작품이 없습니다")
    hidden = weekday_diagnostics.hidden_excluded_count()
    if hidden:
        _say(f"제외됨 완결·서비스 종료·판매 중지 웹툰 {hidden}개는 제외됨 목록에 표시하지 않습니다(필요하면 수동 다운로드에서 분석해서 구독할 수 있습니다)")


async def run_quick_scan_nightly() -> None:
    """매일 새벽 자동 실행 — 전부, 12시간 안에 확인한 작품은 건너뛴다."""
    await run_quick_scan_job(None, NIGHTLY_MAX_AGE_HOURS)
