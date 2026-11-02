"""기다무 충전 시각 다운로드 — 1분마다 저장된 충전 시각만 보고(서버 요청 없음), 시각이 지난 작품만 한 개씩 받는다.

"기다무 충전 시각에 맞춰 자동 다운로드" 옵션이 켜져 있을 때만 등록된다(schedule.apply_waitfree_job). 받을 작품이 없으면 서버에 요청도, 작업 상태/이력도 만들지 않는다.
정기/수동 다운로드가 돌고 있으면 이번 틱은 건너뛴다(같은 작품을 동시에 받지 않는다 — 1분 뒤 다시 확인). 같은 작품을 연속으로 두드리지 않도록
충전 시각이 그대로면 10분, 오류/로그인 문제면 30분 뒤로 미룬다(waitfree_sync).
"""
import logging
from datetime import datetime, timezone

from app import job_status, repository, waitfree_sync
from app.config import get_settings
from app.scheduler.download_job import _download_job_lock
from app.scheduler.kakao_download import _download_kakao_subscriptions

log = logging.getLogger(__name__)


async def run_waitfree_due_job(now: datetime | None = None) -> None:
    if not waitfree_sync.is_enabled():
        return
    now = now or datetime.now(timezone.utc)
    due = waitfree_sync.due_webtoons(now)
    if not due:
        return  # 서버에 요청도, 작업 상태/이력도 만들지 않는다
    if _download_job_lock.locked():
        return  # 정기/수동 다운로드가 돌고 있다 — 같은 작품을 동시에 받지 않도록 1분 뒤에 다시 확인한다
    async with _download_job_lock:
        settings = get_settings()
        job_status.start("download")
        job_status.log_line("download", f"기다무 충전 시각이 된 작품 {len(due)}개를 받습니다: " + ", ".join(w["title"] for w in due))
        had_error = False
        outcomes: list = []
        try:
            outcomes = await _download_kakao_subscriptions(settings, [], only=due, now=now)
        except Exception as e:
            had_error = True
            log.error("기다무 충전 시각 다운로드 중 예외: %s", e)
            job_status.log_line("download", f"기다무 충전 시각 다운로드 중 오류: {e}")
        done = {webtoon["title_id"]: result for webtoon, result in outcomes}
        for webtoon in due:
            result = done.get(webtoon["title_id"])
            failed = webtoon["title_id"] not in done or result is None or getattr(result, "error", None) is not None
            current = repository.get_kakao_webtoon(webtoon["title_id"]) or webtoon
            next_at = current.get("waitfree_next_at")
            if failed:
                waitfree_sync.delay(webtoon["title_id"], waitfree_sync.ERROR_MINUTES, now)  # 오류/로그인 문제 — 같은 작품을 계속 두드리지 않는다
                had_error = had_error or webtoon["title_id"] in done
            elif next_at and not waitfree_sync.is_waiting(current, now):
                waitfree_sync.delay(webtoon["title_id"], waitfree_sync.RETRY_MINUTES, now)  # 받았는데 충전 시각이 그대로 — 서버 반영이 늦은 것이니 잠시 뒤에 다시
        job_status.finish("download", success=not had_error)
