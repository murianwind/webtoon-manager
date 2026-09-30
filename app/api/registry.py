"""작가/태그 자동추가 레지스트리(네이버+카카오): 관심 작가/태그 등록·해제, 후보 검색, 전체 재동기화."""


import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app import (
    job_status,
    kakao_page_download,
    naver_api,
    repository,
    tracker,
)
from app import kakao_api
from app.config import get_settings



log = logging.getLogger(__name__)

router = APIRouter()



class WatchedAuthorOut(BaseModel):
    author_id: str
    author_name: str
    enabled: bool


class WatchedTagOut(BaseModel):
    tag_id: str
    tag_name: str
    enabled: bool


class WatchedAuthorIn(BaseModel):
    author_id: str
    author_name: str = ""

    @field_validator("author_id")
    @classmethod
    def author_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("author_id가 비어있습니다.")
        return v.strip()


class WatchedTagIn(BaseModel):
    tag_id: str
    tag_name: str = ""

    @field_validator("tag_id")
    @classmethod
    def tag_id_numeric(cls, v: str) -> str:
        if not v.strip().isdigit():
            raise ValueError("tag_id는 숫자만 입력할 수 있습니다.")
        return v.strip()


class InterestedAuthorOut(BaseModel):
    author_id: str
    author_name: str
    enabled: bool
    is_origin: bool = False  # 원작자(작품에 작가와 원작자가 따로 있을 때의 원작자)


@router.get("/authors/interested", response_model=list[InterestedAuthorOut])
async def list_interested_authors():
    """
    "등록된 작가" = watched_authors에서 enabled=1 / "전체 작가 목록" = enabled=0.
    이름은 watched_authors에 있으면 그걸 쓰고, 비어있으면 DB의 웹툰 어딘가에
    저장된 실제 이름으로 보정한다 (예전에 이름 없이 등록됐던 것도 여기서 채워짐).
    """
    watched = await asyncio.to_thread(repository.list_watched_authors)
    all_pairs = await asyncio.to_thread(repository.list_all_writer_id_name_pairs)
    origin_ids = await asyncio.to_thread(repository.list_origin_author_ids)

    result_map: dict[str, InterestedAuthorOut] = {}
    for a in watched:
        name = a.author_name or all_pairs.get(a.author_id, "")
        result_map[a.author_id] = InterestedAuthorOut(
            author_id=a.author_id, author_name=name, enabled=a.enabled, is_origin=a.author_id in origin_ids
        )

    # watched_authors에 아직 한 번도 안 들어간 저자(웹툰 데이터에만 있는 경우 — 작가와 원작자 모두)는
    # "전체 작가 목록"(미등록) 쪽에 기본으로 채운다.
    for author_id, author_name in all_pairs.items():
        if author_id not in result_map:
            result_map[author_id] = InterestedAuthorOut(
                author_id=author_id, author_name=author_name, enabled=False, is_origin=author_id in origin_ids
            )

    return sorted(result_map.values(), key=lambda a: a.author_name)


@router.get("/watched-authors", response_model=list[WatchedAuthorOut])
async def list_watched_authors():
    rows = await asyncio.to_thread(repository.list_watched_authors)
    return [WatchedAuthorOut(author_id=r.author_id, author_name=r.author_name, enabled=r.enabled) for r in rows]


@router.post("/watched-authors", response_model=WatchedAuthorOut)
async def add_watched_author(payload: WatchedAuthorIn):
    await asyncio.to_thread(repository.upsert_watched_author, payload.author_id, payload.author_name, True)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next(r for r in rows if r.author_id == payload.author_id)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


class AuthorEnableIn(BaseModel):
    author_name: str = ""  # 아직 watched_authors에 없는 작가를 처음 등록할 때 이름을 같이 넘긴다


@router.post("/watched-authors/{author_id}/enable", response_model=WatchedAuthorOut)
async def enable_watched_author(author_id: str, payload: AuthorEnableIn | None = None):
    author_name = payload.author_name if payload else ""
    await asyncio.to_thread(repository.set_watched_author_enabled, author_id, True, author_name)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next(r for r in rows if r.author_id == author_id)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.post("/watched-authors/{author_id}/disable", response_model=WatchedAuthorOut)
async def disable_watched_author(author_id: str, payload: AuthorEnableIn | None = None):
    author_name = payload.author_name if payload else ""
    await asyncio.to_thread(repository.set_watched_author_enabled, author_id, False, author_name)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next(r for r in rows if r.author_id == author_id)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.delete("/watched-authors/{author_id}")
async def remove_watched_author(author_id: str):
    """레지스트리에서 완전히 지운다 (이름 없이 남은 예전 데이터 등을 정리할 때 사용)."""
    await asyncio.to_thread(repository.delete_watched_author, author_id)
    return {"status": "deleted"}


class KakaoWatchedAuthorIn(BaseModel):
    author_name: str

    @field_validator("author_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("작가 이름이 비어있습니다.")
        return v.strip()


@router.get("/kakao/watched-authors", response_model=list[WatchedAuthorOut])
async def list_kakao_watched_authors():
    rows = await asyncio.to_thread(repository.list_watched_authors, "kakao")
    return [WatchedAuthorOut(author_id=r.author_id, author_name=r.author_name, enabled=r.enabled) for r in rows]


@router.post("/kakao/watched-authors", response_model=WatchedAuthorOut)
async def add_kakao_watched_author(payload: KakaoWatchedAuthorIn):
    """등록 즉시 실제로 그 이름의 작품이 있는지 카카오에 확인한다 — 오타로 존재하지
    않는 이름을 등록해버리는 걸 막기 위해서다(네이버처럼 검색 결과에서 골라 등록하는
    방식이 아니라 이름을 직접 입력받으므로, 여기서 확인 안 하면 오타를 못 잡는다)."""
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        results = await kakao_api.search_by_author(session, payload.author_name, settings.request_timeout_seconds)
    if not results:
        raise HTTPException(status_code=404, detail=f"'{payload.author_name}' 이름으로 카카오웹툰에서 작품을 찾지 못했습니다.")

    await asyncio.to_thread(repository.upsert_watched_author, payload.author_name, payload.author_name, True, "kakao")
    rows = await asyncio.to_thread(repository.list_watched_authors, "kakao")
    match = next(r for r in rows if r.author_id == payload.author_name)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.post("/kakao/watched-authors/{author_name}/enable", response_model=WatchedAuthorOut)
async def enable_kakao_watched_author(author_name: str):
    await asyncio.to_thread(repository.set_watched_author_enabled, author_name, True, author_name, "kakao")
    rows = await asyncio.to_thread(repository.list_watched_authors, "kakao")
    match = next(r for r in rows if r.author_id == author_name)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.post("/kakao/watched-authors/{author_name}/disable", response_model=WatchedAuthorOut)
async def disable_kakao_watched_author(author_name: str):
    await asyncio.to_thread(repository.set_watched_author_enabled, author_name, False, author_name, "kakao")
    rows = await asyncio.to_thread(repository.list_watched_authors, "kakao")
    match = next(r for r in rows if r.author_id == author_name)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.delete("/kakao/watched-authors/{author_name}")
async def remove_kakao_watched_author(author_name: str):
    await asyncio.to_thread(repository.delete_watched_author, author_name, "kakao")
    return {"status": "deleted"}


@router.get("/kakao/authors/candidates")
async def list_kakao_author_candidates():
    """네이버의 /authors/candidates와 동일한 역할 — 지금 연재 중인 요일별 목록에서 저자 이름
    후보를 뽑는다(요일별 목록은 10분 캐시라 대개 바로 나오고, 캐시가 없으면 몇 초 걸린다).
    완결작 작가는 여기 안 나오지만, 이름을 직접 입력해 검색해서 등록하는 건 그대로 된다."""
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        items = await kakao_api.fetch_weekday_catalog(session, settings.request_timeout_seconds)
    return kakao_api.extract_candidate_author_names(items)


@router.get("/watched-tags", response_model=list[WatchedTagOut])
async def list_watched_tags():
    rows = await asyncio.to_thread(repository.list_watched_tags)
    return [WatchedTagOut(tag_id=r.tag_id, tag_name=r.tag_name, enabled=r.enabled) for r in rows]


@router.post("/watched-tags", response_model=WatchedTagOut)
async def add_watched_tag(payload: WatchedTagIn):
    tag_name = payload.tag_name
    if not tag_name:
        settings = get_settings()
        async with aiohttp.ClientSession() as session:
            tag_name = await naver_api.fetch_curation_title_name(
                session, int(payload.tag_id), settings.request_timeout_seconds
            )
    await asyncio.to_thread(repository.upsert_watched_tag, payload.tag_id, tag_name, True)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next(r for r in rows if r.tag_id == payload.tag_id)
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.post("/watched-tags/{tag_id}/enable", response_model=WatchedTagOut)
async def enable_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.set_watched_tag_enabled, tag_id, True)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next((r for r in rows if r.tag_id == tag_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 태그입니다.")
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.post("/watched-tags/{tag_id}/disable", response_model=WatchedTagOut)
async def disable_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.set_watched_tag_enabled, tag_id, False)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next((r for r in rows if r.tag_id == tag_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 태그입니다.")
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.delete("/watched-tags/{tag_id}")
async def remove_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.delete_watched_tag, tag_id)
    return {"status": "deleted"}


@router.get("/tags/catalog")
async def get_tag_catalog():
    """네이버가 제공하는 전체 태그 목록 (이름으로 골라서 등록할 때 사용)."""
    settings = get_settings()
    try:
        async with aiohttp.ClientSession() as session:
            return await naver_api.fetch_tag_catalog(session, settings.request_timeout_seconds)
    except naver_api.NaverApiError as e:
        raise HTTPException(status_code=502, detail=f"태그 카탈로그를 불러오지 못했습니다: {e}")


@router.get("/authors/search")
async def search_authors(name: str):
    """작가 이름으로 검색 — 네이버 통합검색으로 실제 author_id를 알아낸다."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="검색할 이름을 입력해주세요.")
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        return await tracker.search_authors_by_name(session, name.strip(), settings)


@router.post("/registry/resync")
async def resync_registry():
    """지금 추적 중인 모든 웹툰을 훑어서 작가/태그 레지스트리를 즉시 채운다. 카카오웹툰 관리를 켰으면 카카오페이지 작품의
    작가(글/원작 구분)도 같은 규칙으로 함께 채운다."""
    async def _run():
        settings = get_settings()
        job_status.start("registry")
        job_status.log_line("registry", "작가/태그 재동기화 시작")
        try:
            async with aiohttp.ClientSession() as session:
                count = await tracker.resync_registry(session, settings)
            kakao_count = None
            if await asyncio.to_thread(repository.get_setting, "kakao_webtoons_enabled") == "1":
                job_status.log_line("registry", "카카오 작가 재동기화 시작")
                async with kakao_page_download.new_session() as kakao_session:
                    kakao_client = kakao_page_download.client_or_anonymous(kakao_session, settings.request_timeout_seconds)
                    kakao_count = await tracker.resync_kakao_registry(kakao_client, settings)
                    kakao_page_download.persist_refreshed_cookies(kakao_client)
            job_status.log_line("registry", f"{count}개 웹툰 처리 완료" + ("" if kakao_count is None else f", 카카오 {kakao_count}개 처리 완료"))
            job_status.finish("registry", success=True)
        except Exception as e:
            job_status.log_line("registry", f"오류: {e}")
            job_status.finish("registry", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/authors/candidates")
async def list_author_candidates():
    """
    이미 불러온 네이버 전체목록의 저자 텍스트에서 이름 후보를 즉시 뽑아 보여준다.
    추가 API 호출이 없어서 빠르다 — '전체 작가 목록'은 이 목록에서 이미 등록된
    이름을 뺀 것으로 표시하고, 실제 등록은 이름 검색으로 정확한 id를 확인해서 한다.
    """
    settings = get_settings()
    try:
        async with aiohttp.ClientSession() as session:
            items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except naver_api.NaverApiError as e:
        raise HTTPException(status_code=502, detail=f"작가 후보 목록을 불러오지 못했습니다: {e}")
    return naver_api.extract_candidate_author_names(items)
