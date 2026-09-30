"""카카오웹툰: 전체 목록(캐시), 표지, 구독/구독해제/제외/삭제."""


import asyncio
import hashlib
import json
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app import (
    kakao_page_download,
    repository,
)
from app import kakao_api
from app import kakao_catalog
from app import kakao_cover
from app import archiver
from app.config import get_settings

from app.api.common import (
    _is_author_auto_register_enabled,
    _render_exclude_confirm_html,
)


log = logging.getLogger(__name__)

router = APIRouter()



class KakaoWebtoonOut(BaseModel):
    title_id: int
    title: str
    status: str
    ever_subscribed: bool
    thumbnail_url: str = ""
    author_summary: str = ""
    is_new: bool = False
    is_paused: bool = False
    has_new_episode: bool = False


class KakaoWebtoonEntryIn(BaseModel):
    title: str
    thumbnail_url: str = ""
    author_summary: str = ""


# 목록의 카카오 썸네일을 처음 볼 때 작품마다 카카오 API를 한 번씩 불러야 해서(요일 목록 카드에는 공식
# 표지가 없다), 화면에 한꺼번에 보이는 수십 개가 동시에 요청되면 카카오 쪽에서 막힐 수 있다 — 서로 다른
# 작품은 동시에 2개까지만 받고 나머지는 줄을 세운다. 같은 작품이 동시에 여러 번 요청돼도(화면을
# 다시 그릴 때 등) 작품별 락으로 한 번만 받는다. 한 번 받은 표지는 파일로 저장해서 이후엔 카카오를
# 안 부른다.
_kakao_thumbnail_semaphore = asyncio.Semaphore(2)


_kakao_thumbnail_locks: dict[int, asyncio.Lock] = {}


_KAKAO_THUMBNAIL_BROWSER_CACHE = "public, max-age=86400"


@router.get("/kakao-thumbnail/{series_id}")
async def get_kakao_thumbnail(series_id: int):
    """"웹툰 전체목록"의 카카오 카드 썸네일 — 카카오페이지 작품 페이지에 나오는 공식 표지(제목이 들어간
    완성 이미지)를 줄인 크기로 받아 저장해두고 서빙한다. 요일 목록 카드의 이미지는 그 작품의 공식
    표지와 다른 그림이라 이 방식을 쓴다. 받지 못하면 404 — 화면이 목록 카드 이미지로 대신 보여준다."""
    cache_path = kakao_cover.thumbnail_cache_dir(get_settings().database_path) / f"{series_id}.jpg"
    if not cache_path.is_file():
        lock = _kakao_thumbnail_locks.setdefault(series_id, asyncio.Lock())
        try:
            async with lock:
                if not cache_path.is_file():  # 줄 서서 기다리는 사이 같은 작품 요청이 이미 채웠을 수 있다
                    async with _kakao_thumbnail_semaphore:
                        jpeg_bytes = await asyncio.to_thread(
                            kakao_cover.fetch_official_cover_bytes, str(series_id), filename=kakao_api.IMAGE_FILENAME_CARD
                        )
                    if jpeg_bytes is None:
                        raise HTTPException(status_code=404, detail="카카오페이지 표지를 받지 못했습니다.")
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(jpeg_bytes)
        finally:
            _kakao_thumbnail_locks.pop(series_id, None)
    return FileResponse(cache_path, media_type="image/jpeg", headers={"Cache-Control": _KAKAO_THUMBNAIL_BROWSER_CACHE})


@router.get("/kakao-list")
async def browse_kakao_list(refresh: bool = False):
    """"웹툰 전체목록"에 카카오페이지 웹툰을 같이 보여주기 위한 목록. **캐시에서만 읽어서 바로 돌려준다**(네트워크를
    기다리지 않음) — 목록은 프로그램 시작 때, 3시간마다, 그리고 refresh=true(새로고침 버튼)일 때 백그라운드로
    다시 채워진다(kakao_catalog 참고). 응답의 refreshing이 true면 채우는 중이라는 뜻이고, 화면은 조금 뒤 다시
    불러서 version이 바뀌었을 때만 화면을 갱신한다.
    추적 중(구독/구독해제/제외)인 것은 그 상태를 같이 붙이고, "제외됨"은 걸러낸다. 요일별 목록에서 사라진(장기
    휴재 등) 구독 이력 있는 작품은 DB 기록으로 보완해서 계속 보여준다."""
    if refresh:
        kakao_catalog.start_refresh()
    else:
        kakao_catalog.ensure_fresh()
    # 목록과 "갱신 중인지"는 같은 순간에 읽는다 — 이 아래에서 기다리는 사이 갱신이 끝나도, 응답 안에서 서로 어긋나
    # 지 않게(어긋나면 화면이 "끝났다"고 보고 다시 확인하지 않는다).
    items, fetched_at = kakao_catalog.snapshot()
    refreshing = kakao_catalog.is_refreshing()
    tracked_map = await asyncio.to_thread(repository.get_kakao_webtoons_map)

    seen_ids: set[int] = set()
    result = []
    author_updates: dict[int, str] = {}
    for item in items:
        seen_ids.add(item["title_id"])
        tracked = tracked_map.get(item["title_id"])
        if tracked is not None and tracked["status"] == repository.STATUS_EXCLUDED:
            continue
        author_summary = ", ".join(item["author_names"])
        if tracked is not None and author_summary and author_summary != tracked["author_summary"]:
            # 추적 중인 작품이면, 목록에서 받은 최신 작가 정보로 DB 기록도 갱신해둔다 — 요일별 목록을 다시 안
            # 훑는 "구독해제"/"제외됨" 탭이 예전(혹은 비어있던) 작가 정보만 계속 보이지 않게.
            author_updates[item["title_id"]] = author_summary
        result.append(
            {
                "title_id": item["title_id"], "title": item["title_name"], "thumbnail_url": item["thumbnail_url"],
                "is_adult": item["is_adult"], "author_summary": author_summary, "is_new": item["is_new"],
                "is_paused": item["is_paused"], "has_new_episode": item["has_update"],
                "status": tracked["status"] if tracked else None,
                "ever_subscribed": tracked["ever_subscribed"] if tracked else False,
            }
        )
    for title_id, summary in author_updates.items():
        await asyncio.to_thread(repository.refresh_kakao_webtoon_author_summary, title_id, summary)

    # 요일별 목록엔 지금 연재 중인 것만 나오므로, 장기 휴재 등으로 거기서 빠진 구독 이력 있는 작품은 DB 기록으로
    # 보완해서 계속 보여준다(제외됨은 계속 숨김). 카탈로그가 아직 비어 있는 첫 실행 중에는 이 보완이 목록 전체가 된다.
    for wt in tracked_map.values():
        if wt["title_id"] in seen_ids or wt["status"] not in (
            repository.STATUS_ACTIVE, repository.STATUS_UNSUBSCRIBED, repository.STATUS_UNREGISTERED,
        ):
            continue
        result.append(
            {
                "title_id": wt["title_id"], "title": wt["title"], "thumbnail_url": wt["thumbnail_url"], "is_adult": False,
                "author_summary": wt["author_summary"], "is_new": False, "is_paused": False, "has_new_episode": False,
                "status": wt["status"], "ever_subscribed": wt["ever_subscribed"],
            }
        )
    version = hashlib.sha1(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {
        "items": result, "version": version, "refreshing": refreshing,
        "refreshed_at": datetime.fromtimestamp(fetched_at, tz=timezone.utc).isoformat() if fetched_at else None,
    }


@router.get("/kakao-webtoons", response_model=list[KakaoWebtoonOut])
async def list_kakao_webtoons(status: str | None = None):
    """"구독해제"/"제외됨" 탭에서 네이버 목록과 합쳐서 보여줄 카카오 목록. 신작/UP/휴재
    배지와 저자 정보도 요일별 목록과 대조해서 채워준다(지금도 연재 중인 것만 매칭되고,
    거기 없으면 — 장기 휴재 등 — DB에 저장된 값 그대로 나간다. browse_kakao_list의
    DB 보완 항목과 같은 한계). 저자 정보가 비어있거나 오래된 채로 저장돼 있었다면
    이 조회 자체가 최신 값으로 갱신해서, 이 탭을 여는 것만으로도 스스로 채워진다."""
    if status and status not in (
        repository.STATUS_ACTIVE, repository.STATUS_UNSUBSCRIBED, repository.STATUS_EXCLUDED,
    ):
        raise HTTPException(status_code=400, detail="status는 active/unsubscribed/excluded 중 하나여야 합니다.")
    rows = await asyncio.to_thread(repository.list_kakao_webtoons_by_status, status) if status else []
    if status == repository.STATUS_UNSUBSCRIBED:
        # webtoons와 동일한 규칙 — 구독한 적 없는 건 "구독해제" 탭에 안 보인다.
        rows = [r for r in rows if r["ever_subscribed"]]
    if not rows:
        return []

    # 배지(UP/신작/휴재)와 작가 정보는 캐시된 요일별 목록에서 보강한다 — 이 탭을 열 때 카카오를 부르지 않는다.
    kakao_catalog.ensure_fresh()
    catalog_by_id = {item["title_id"]: item for item in kakao_catalog.snapshot()[0]}

    result = []
    for r in rows:
        catalog_item = catalog_by_id.get(r["title_id"])
        extra = {}
        if catalog_item is not None:
            extra = {
                "is_new": catalog_item["is_new"], "is_paused": catalog_item["is_paused"],
                "has_new_episode": catalog_item["has_update"],
            }
            fresh_author_summary = ", ".join(catalog_item["author_names"])
            # 저자 정보가 나중에(이 컬럼이 생기기 전 등) 비어있는 채로 저장된 채 계속
            # 남아있는 경우가 있었다 — "제외됨"/"구독해제" 조회할 때도 요일별 목록을
            # 이미 훑고 있으니, 여기서도 최신 정보로 같이 채우고 DB에도 반영해서
            # 다음부터는 이 조회 자체로 매번 자동으로 채워지게 한다("웹툰 전체목록"을
            # 거쳐야만 채워지던 것과 달리, 이 탭만 봐도 스스로 낫는다).
            if fresh_author_summary and fresh_author_summary != r["author_summary"]:
                await asyncio.to_thread(repository.refresh_kakao_webtoon_author_summary, r["title_id"], fresh_author_summary)
                r = {**r, "author_summary": fresh_author_summary}
        result.append(KakaoWebtoonOut(**r, **extra))
    return result


async def _get_or_404_kakao(title_id: int) -> dict:
    wt = await asyncio.to_thread(repository.get_kakao_webtoon, title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 카카오웹툰입니다.")
    return wt


async def _kakao_about(series_id: int) -> dict | None:
    """작품 "정보"(글/그림/원작 작가 등). 못 받으면 None — 호출부가 등록/저장을 건너뛴다."""
    settings = get_settings()
    try:
        async with kakao_page_download.new_session() as session:
            return await kakao_page_download.client_or_anonymous(session, settings.request_timeout_seconds).fetch_about(series_id)
    except Exception as e:
        log.warning("카카오 작품 정보(series_id=%s) 조회 실패 — 작가 등록/저장을 건너뜁니다: %s", series_id, e)
        return None


@router.post("/kakao-webtoons/{title_id}/subscribe", response_model=KakaoWebtoonOut)
async def subscribe_kakao_webtoon(title_id: int, payload: KakaoWebtoonEntryIn):
    """"웹툰 뷰어 서버 주소"가 설정돼 있을 때만 의미 있는 동작 — 다운로드를 뜻하는
    게 아니라, 뷰어로 계속 챙겨보고 싶다는 표시일 뿐이다. 그 설정 여부는 프론트엔드가
    버튼을 보여줄지 말지로 판단하고, 여기서는 굳이 다시 검사하지 않는다."""
    if not await asyncio.to_thread(repository.kakao_webtoon_exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new_kakao_webtoon, title_id, payload.title, payload.thumbnail_url,
            repository.STATUS_ACTIVE, payload.author_summary,
        )
    await asyncio.to_thread(repository.set_kakao_webtoon_status, title_id, repository.STATUS_ACTIVE)
    await asyncio.to_thread(repository.refresh_kakao_webtoon_author_summary, title_id, payload.author_summary)
    about = await _kakao_about(title_id)
    # 글 작가를 저장해 둔다 — 아카이빙 파일명 템플릿의 {author}가 네이버처럼 "글" 작가가 되게(작품 정보를 못 받으면 받을 때 채워진다)
    await asyncio.to_thread(repository.set_kakao_writer_names, title_id, kakao_page_download.split_authors(about)[0])
    if await asyncio.to_thread(_is_author_auto_register_enabled):
        # 구독하면 그 작품의 작가를 관심 작가로 자동 등록한다(네이버와 같은 설정, 같은 규칙): 작품 "정보" 탭의 글/그림/원작
        # 구분으로 원작자가 있으면 원작자를, 없으면 글 작가를 등록한다. 정보 탭을 못 받으면 등록하지 않는다(그림 작가 등을
        # 잘못 등록하지 않도록 이름 전부를 등록하는 대신 건너뛴다).
        for author_name in kakao_page_download.authors_to_register(about):
            await asyncio.to_thread(repository.upsert_watched_author, author_name, author_name, True, "kakao")
    return KakaoWebtoonOut(**await asyncio.to_thread(repository.get_kakao_webtoon, title_id))


@router.post("/kakao-webtoons/{title_id}/unsubscribe", response_model=KakaoWebtoonOut)
async def unsubscribe_kakao_webtoon(title_id: int):
    webtoon = await _get_or_404_kakao(title_id)
    await asyncio.to_thread(repository.set_kakao_webtoon_status, title_id, repository.STATUS_UNSUBSCRIBED)
    # 완결이고 받을 회차를 다 받은 작품을 구독해제하면 네이버와 같이 이동 대기열에 넣는다(설정이 켜져 있을 때)
    await asyncio.to_thread(archiver.queue_finish_archive_if_applicable, f"{archiver.KAKAO_TARGET_PREFIX}{title_id}", webtoon["is_finished"])
    return KakaoWebtoonOut(**await asyncio.to_thread(repository.get_kakao_webtoon, title_id))


@router.post("/kakao-webtoons/{title_id}/exclude", response_model=KakaoWebtoonOut)
async def exclude_kakao_webtoon(title_id: int, payload: KakaoWebtoonEntryIn):
    if not await asyncio.to_thread(repository.kakao_webtoon_exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new_kakao_webtoon, title_id, payload.title, payload.thumbnail_url,
            repository.STATUS_EXCLUDED, payload.author_summary,
        )
    await asyncio.to_thread(repository.set_kakao_webtoon_status, title_id, repository.STATUS_EXCLUDED)
    await asyncio.to_thread(repository.refresh_kakao_webtoon_author_summary, title_id, payload.author_summary)
    return KakaoWebtoonOut(**await asyncio.to_thread(repository.get_kakao_webtoon, title_id))


@router.post("/kakao-webtoons/{title_id}/unregister", response_model=KakaoWebtoonOut)
async def unregister_kakao_webtoon(title_id: int):
    """"목록으로" — 구독 이력이 있으면(webtoons.unregister와 같은 규칙) 완전 삭제
    대신 전용 상태로 옮겨서 기록을 남긴다. 구독 이력이 없는 건 이 엔드포인트 대신
    DELETE로 완전히 지운다(프론트엔드가 ever_subscribed 값으로 어느 쪽을 부를지 정함)."""
    wt = await _get_or_404_kakao(title_id)
    if not wt["ever_subscribed"]:
        raise HTTPException(status_code=400, detail="구독 이력이 없는 작품은 완전 삭제를 사용하세요.")
    await asyncio.to_thread(repository.set_kakao_webtoon_status, title_id, repository.STATUS_UNREGISTERED)
    return KakaoWebtoonOut(**await asyncio.to_thread(repository.get_kakao_webtoon, title_id))


@router.delete("/kakao-webtoons/{title_id}")
async def delete_kakao_webtoon(title_id: int):
    wt = await _get_or_404_kakao(title_id)
    if wt["status"] == repository.STATUS_ACTIVE:
        raise HTTPException(status_code=400, detail="구독 중인 웹툰은 완전 삭제할 수 없습니다.")
    await asyncio.to_thread(repository.hard_delete_kakao_webtoon, title_id)
    return {"status": "deleted"}


@router.get("/kakao-webtoons/{title_id}/exclude-confirm", response_class=HTMLResponse)
async def kakao_exclude_confirm_page(title_id: int, title: str = ""):
    return _render_exclude_confirm_html(
        title_id, title, f"/api/kakao-webtoons/{title_id}/exclude", {"title": title or str(title_id)},
    )
