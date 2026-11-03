"""전체목록(네이버) 만들기 — 네이버 요일별 목록과 우리 DB를 합쳐서 화면용 항목으로 돌려준다(api/webtoons의 /naver-list가 부른다).

하는 일: ① 네이버 요일별 목록과 매일+ 목록을 받는다 ② 추적 중인 작품의 up 값을 DB에 갱신한다 ③ 제외됨/구독해제 탭이 쓰도록 요일을 저장한다
④ 네이버가 요일별 목록에서 빼버린 추적 작품(장기 휴재 등)은 DB 정보로 채워서 계속 보이게 하고, 그 작품들의 요일은 작품 페이지에서 백그라운드로 알아낸다(weekday_lookup).
"""
import asyncio
import logging
import time

import aiohttp

from app import daily_plus, naver_api, repository, weekday_lookup, weekdays
from app.config import Settings

log = logging.getLogger(__name__)

SLOW_LOG_SECONDS = 2.0  # 이보다 오래 걸린 날만 단계별 시간을 로그에 남긴다
_now = time.perf_counter  # 테스트에서 시계를 바꿀 수 있게 따로 뒀다


async def build_naver_list(settings: Settings) -> list[dict]:
    """네이버 목록을 받지 못하면 naver_api.NaverApiError가 그대로 올라간다(호출하는 쪽이 502로 바꾼다).
    오래 걸리면(SLOW_LOG_SECONDS 이상) 어느 단계(네이버 목록 받기/DB 읽기/합치기)에서 걸렸는지 로그에 남긴다 — 느린 이유를 추측하지 않고 실제 병목을 보기 위해."""
    started = _now()
    async with aiohttp.ClientSession() as session:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
        hidden = await daily_plus.current_ids(settings.request_timeout_seconds, session)
    items = [item for item in items if item.title_id not in hidden]  # 매일+ 작품은 목록에 보이지 않는다(구독 중이어도)

    fetched = _now()
    existing = await asyncio.to_thread(repository.list_all)
    loaded = _now()
    existing_by_id = {w.title_id: w for w in existing}
    seen_ids = {item.title_id for item in items}

    result = [await _live_entry(item, existing_by_id.get(item.title_id)) for item in items]

    # 제외됨/구독해제 탭은 네이버를 부르지 않고 DB 기록으로 목록을 만든다 — 그 탭들이 요일별 탭에 쓰도록 요일을 저장해 둔다(바뀐 게 있을 때만 쓴다).
    await asyncio.to_thread(weekdays.save_naver_map, {entry["title_id"]: entry["weekdays"] for entry in result})

    # 네이버의 요일별 목록은 장기 휴재작을 응답에서 아예 뺀다 — DB에만 남아 있는 작품은 우리가 가진 정보로 채워서라도 계속 보이게 하고,
    # 요일도 목록에서 빠지므로(그대로면 "기타") 완결이 아닌 추적 작품은 작품 페이지에서 요일을 알아낸다(백그라운드 — 알아낸 요일은 다음에 불러올 때부터).
    days_by_id = await asyncio.to_thread(weekdays.get_naver_map)
    missing = [wt.title_id for wt in existing if wt.title_id not in seen_ids and wt.title_id not in hidden and not wt.is_finished]
    weekday_lookup.start(await asyncio.to_thread(weekdays.pending_lookup_ids, missing), settings.request_timeout_seconds)
    result.extend(_db_only_entry(wt, days_by_id) for wt in existing if _is_db_only_shown(wt, seen_ids, hidden))
    finished = _now()
    if finished - started >= SLOW_LOG_SECONDS:
        log.info("전체목록 만들기 %.1f초 — 네이버 목록 %.1f초, DB %.1f초, 합치기 %.1f초", finished - started, fetched - started, loaded - fetched, finished - loaded)
    return result


async def _live_entry(item, tracked) -> dict:
    """네이버 목록의 작품 하나 + 우리 DB의 구독 상태. 추적 중인 작품이면 이 목록을 훑는 김에 실제 네이버 up 값을 DB에 갱신해 둔다
    (개별 작품 API에는 이 값이 없어서, 다른 탭(구독해제/제외됨 등)에서 항상 최신값을 보여주려면 여기서 갱신하는 방법뿐이다)."""
    if tracked is not None and tracked.has_update != item.has_update:
        await asyncio.to_thread(repository.update_has_update, item.title_id, item.has_update)
    return {
        "title_id": item.title_id,
        "title": item.title_name,
        "thumbnail_url": tracked.thumbnail_url if tracked and tracked.thumbnail_url else item.thumbnail_url,
        "weekdays": weekdays.from_naver(item.weekdays),
        "is_finished": item.is_finished,
        "is_paused": item.is_paused,
        "is_new": item.is_new,
        "is_adult": item.is_adult,
        "author_summary": item.author_summary,
        "status": tracked.status if tracked else None,
        "ever_subscribed": tracked.ever_subscribed if tracked else False,
        "genres": tracked.genres if tracked else [],
        "tags": tracked.tags if tracked else [],
        "has_new_episode": item.has_update,
    }


def _is_db_only_shown(wt, seen_ids: set[str], hidden: set[str]) -> bool:
    """네이버 목록에 없는 추적 작품 중 이 목록에 보일 것 — 제외됨(전용 탭이 있다)과 매일+는 뺀다."""
    return wt.title_id not in seen_ids and wt.status != repository.STATUS_EXCLUDED and wt.title_id not in hidden


def _db_only_entry(wt, days_by_id: dict[str, list[str]]) -> dict:
    return {
        "title_id": wt.title_id,
        "title": wt.title,
        "thumbnail_url": wt.thumbnail_url,
        "weekdays": days_by_id.get(wt.title_id, []),
        "is_finished": wt.is_finished,
        "is_paused": wt.is_paused,
        "is_new": wt.is_new,
        "is_adult": wt.is_adult,
        "author_summary": ", ".join(wt.writer_names),
        "status": wt.status,
        "ever_subscribed": wt.ever_subscribed,
        "genres": wt.genres,
        "tags": wt.tags,
        "has_new_episode": wt.has_update,
    }
