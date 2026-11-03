"""요일별 목록에서 빠진 추적 작품(장기 휴재 등)의 요일을 백그라운드로 알아낸다(작품 정보 API 우선, 없으면 모바일 작품 페이지 — naver_api.fetch_title_weekdays).

네이버의 요일별 목록은 오래 쉬는 작품을 빼서, 그런 작품은 요일을 몰라 "기타"에 남는다. 전체목록을 불러올 때(naver_list_service) 알아볼 작품을 골라 여기서 시작하면
응답을 늦추지 않게 백그라운드로 한 번에 weekdays.LOOKUP_BATCH개까지 작품 페이지를 읽어 저장한다(알아낸 요일은 다음에 목록을 불러올 때부터 나온다).
이미 돌고 있으면 또 시작하지 않는다 — 목록을 연달아 불러도 같은 작품을 겹쳐 묻지 않게.
"""
import asyncio
import logging

import aiohttp

from app import naver_api, repository, weekdays
from app.models import TitlePage

log = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def start(title_ids: list[str], timeout_seconds: int) -> None:
    """알아볼 작품이 있으면 백그라운드로 시작한다(이미 돌고 있으면 또 시작하지 않는다)."""
    global _task
    if not title_ids or (_task is not None and not _task.done()):
        return
    _task = asyncio.ensure_future(run(title_ids[: weekdays.LOOKUP_BATCH], timeout_seconds))


def apply_page(title_id: str, page: TitlePage) -> None:
    """조회 결과를 저장한다(실패는 days=None) — 백그라운드 조회와 빠른 상태 스캔이 같이 쓴다. 완결이라고 알려 주면 제외됨/구독해제 작품은 완결로 저장해 화면에서 가린다
    (구독 중인 작품은 신작 스캔이 맡는다). 휴재 여부(paused)는 알 때만(None이 아닐 때) 추적 작품에 그대로 저장한다."""
    weekdays.save_lookup(title_id, page.days if page.status == 200 else None, http=page.status, finished=page.finished)
    tracked = repository.get(title_id)
    if tracked is None:
        return
    if page.finished and tracked.status != repository.STATUS_ACTIVE:
        repository.update_is_finished(title_id, True)
    if page.paused is not None:
        repository.update_is_paused(title_id, page.paused)


async def run(title_ids: list[str], timeout_seconds: int) -> None:
    """작품을 차례로 조회해서 저장한다. 한 작품이 예외로 실패해도 나머지는 계속한다(못 알아낸 것으로 기록 → 하루 뒤에 다시 시도)."""
    try:
        async with aiohttp.ClientSession() as session:
            for title_id in title_ids:
                try:
                    page = await naver_api.fetch_title_weekdays(session, title_id, timeout_seconds)
                except Exception as e:
                    log.warning("작품 요일 조회 실패 (titleId=%s): %s", title_id, e)
                    page = TitlePage(status=None)
                await asyncio.to_thread(apply_page, title_id, page)
                await asyncio.sleep(weekdays.LOOKUP_PAUSE_SECONDS)
    except Exception:
        log.exception("작품 요일 조회 중 오류")
