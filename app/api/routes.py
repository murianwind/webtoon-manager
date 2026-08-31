"""
웹 페이지가 사용하는 REST API.

LAN 전용, 인증 없음. 입력값 검증 실패 시 크래시 대신 명확한 4xx 응답을 준다.

- 구독중/구독해제/제외됨 조회 및 상태 전환 : /webtoons/*
- 네이버 전체 웹툰 목록 조회 + 거기서 바로 구독/제외 : /naver-list/*
- 작가/태그 자동추가 레지스트리 관리 : /watched-authors/*, /watched-tags/*
- 수동 다운로드 : /manual-download/*
- 다운로드/스캔 스케줄 설정 : /settings
- 디스코드 설정 + 테스트 : /settings/discord*
- 백업/복원 : /backup, /restore
- 수동 실행 + 진행상황 조회 : /jobs/*

정렬/검색은 데이터 양이 많지 않아 서버에서 별도 파라미터를 두지 않고
프론트엔드에서 이미 받아온 목록을 가지고 처리한다 (요청 왕복을 줄임).
"""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import (
    discord_bot,
    discord_config,
    discord_notify,
    job_status,
    manual_download,
    naver_api,
    repository,
    schedule_config,
    tracker,
)
from app import scheduler as scheduler_mod
from app import kakao_api
from app import webtoon_server_client
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class WebtoonOut(BaseModel):
    title_id: str
    title: str
    status: str
    is_adult: bool
    added_source: str
    last_downloaded_no: int
    is_finished: bool
    finish_ack: bool
    thumbnail_url: str
    genres: list[str]
    tags: list[str]
    latest_episode_no: int
    is_paused: bool
    is_new: bool
    has_new_episode: bool
    writer_ids: list[str]
    writer_names: list[str]


def _to_out(wt) -> WebtoonOut:
    return WebtoonOut(
        title_id=wt.title_id,
        title=wt.title,
        status=wt.status,
        is_adult=wt.is_adult,
        added_source=wt.added_source,
        last_downloaded_no=wt.last_downloaded_no,
        is_finished=wt.is_finished,
        finish_ack=wt.finish_ack,
        thumbnail_url=wt.thumbnail_url,
        genres=wt.genres,
        tags=wt.tags,
        latest_episode_no=wt.latest_episode_no,
        is_paused=wt.is_paused,
        is_new=wt.is_new,
        has_new_episode=wt.latest_episode_no > wt.last_downloaded_no > 0,
        writer_ids=wt.writer_ids,
        writer_names=wt.writer_names,
    )


def _get_or_404(title_id: str):
    wt = repository.get(title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="해당 titleId를 목록에서 찾을 수 없습니다.")
    return wt


def _trigger_enrich(title_id: str, register_authors_enabled: bool = True) -> None:
    """
    백그라운드에서 정보/작가등록을 바로 채운다 (다음 정기 스캔까지 기다리지 않음).
    register_authors_enabled=False로 부르면(목록제외/구독해제 시) 이 웹툰의 정보는
    채우되, 그 저자를 "등록된 작가"(자동 신작추가 대상)로 올리지는 않는다 —
    제외한 웹툰의 저자가 엉뚱하게 관심작가로 등록되면 안 되기 때문이다.
    """
    async def _run():
        async with aiohttp.ClientSession() as session:
            await tracker.enrich_one(
                session, title_id, get_settings(), register_authors_enabled=register_authors_enabled
            )

    asyncio.create_task(_run())


# ── 구독중 / 구독해제 / 제외됨 조회·전환 ──────────────────────────────

@router.get("/webtoons", response_model=list[WebtoonOut])
async def list_webtoons(status: str | None = None):
    if status and status not in (
        repository.STATUS_ACTIVE,
        repository.STATUS_UNSUBSCRIBED,
        repository.STATUS_EXCLUDED,
    ):
        raise HTTPException(status_code=400, detail="status는 active/unsubscribed/excluded 중 하나여야 합니다.")
    rows = (
        await asyncio.to_thread(repository.list_by_status, status)
        if status
        else await asyncio.to_thread(repository.list_all)
    )
    return [_to_out(r) for r in rows]


@router.post("/webtoons/{title_id}/subscribe", response_model=WebtoonOut)
async def subscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unsubscribe", response_model=WebtoonOut)
async def unsubscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/acknowledge-finish", response_model=WebtoonOut)
async def acknowledge_finish(title_id: str):
    """알람 제외: 구독은 그대로 유지하고, 완결 알림(구독해제 여부 물어보는 것)만 그만 받는다."""
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.acknowledge_finish, title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.get("/webtoons/pending-completion", response_model=list[WebtoonOut])
async def list_pending_completion():
    """완결됐는데 아직 구독해제/알람제외 처리를 안 한 구독중인 웹툰 목록 (완결 확인 봇이 폴링)."""
    rows = await asyncio.to_thread(repository.list_by_status, repository.STATUS_ACTIVE)
    pending = [r for r in rows if r.is_finished and not r.finish_ack]
    return [_to_out(r) for r in pending]


@router.post("/webtoons/{title_id}/remove", response_model=WebtoonOut)
async def remove_from_unsubscribed(title_id: str):
    """구독해제 탭의 '목록에서 제거' — 완전 삭제가 아니라 excluded로 전환한다.
    hard delete를 해버리면 title_id가 DB에서 사라져서 나중에 작가/태그 자동추가가
    다시 이 작품을 추가해버릴 수 있다 — excluded 상태로 남겨야 "이후엔 등록되지
    않는다"가 보장된다."""
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_EXCLUDED)
    _trigger_enrich(title_id, register_authors_enabled=False)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.delete("/webtoons/{title_id}")
async def delete_webtoon_permanently(title_id: str):
    """제외됨 탭에서 완결작을 완전히 지운다 — 완결작은 자동추가 로직이 애초에 다시
    안 건드리므로(항상 finished 체크로 건너뜀), 이 경우만 안전하게 완전 삭제할 수 있다."""
    webtoon = await asyncio.to_thread(_get_or_404, title_id)
    if webtoon.status != repository.STATUS_EXCLUDED:
        raise HTTPException(status_code=400, detail="제외됨 상태의 웹툰만 완전 삭제할 수 있습니다.")
    await asyncio.to_thread(repository.hard_delete, title_id)
    return {"status": "deleted"}


# ── 네이버 전체 웹툰 목록 (여기서 바로 구독/목록제외) ──────────────────

class NaverListEntryIn(BaseModel):
    title: str
    thumbnail_url: str = ""

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title이 비어있습니다.")
        return v


@router.get("/naver-list")
async def browse_naver_list():
    settings = get_settings()
    try:
        async with aiohttp.ClientSession() as session:
            items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except naver_api.NaverApiError as e:
        raise HTTPException(status_code=502, detail=f"네이버 웹툰 목록을 불러오지 못했습니다: {e}")

    existing = await asyncio.to_thread(repository.list_all)
    existing_by_id = {w.title_id: w for w in existing}
    seen_ids: set[str] = set()

    result = []
    for item in items:
        seen_ids.add(item.title_id)
        tracked = existing_by_id.get(item.title_id)
        result.append(
            {
                "title_id": item.title_id,
                "title": item.title_name,
                "thumbnail_url": tracked.thumbnail_url if tracked and tracked.thumbnail_url else item.thumbnail_url,
                "weekdays": item.weekdays,
                "is_finished": item.is_finished,
                "is_paused": item.is_paused,
                "is_new": item.is_new,
                "is_adult": item.is_adult,
                "author_summary": item.author_summary,
                "status": tracked.status if tracked else None,
                "genres": tracked.genres if tracked else [],
                "tags": tracked.tags if tracked else [],
                "has_new_episode": item.has_update,
            }
        )

    # 네이버의 요일별 목록 API는 장기 휴재작을 응답에서 아예 빼버린다. 그래서 위 루프만
    # 돌면 이미 추적 중인(구독중/구독해제) 웹툰이 화면에서 통째로 사라질 수 있다 —
    # DB에만 남아있는 건 우리가 갖고 있는 정보로 채워서라도 계속 보이게 한다.
    for wt in existing:
        if wt.title_id in seen_ids or wt.status == repository.STATUS_EXCLUDED:
            continue
        result.append(
            {
                "title_id": wt.title_id,
                "title": wt.title,
                "thumbnail_url": wt.thumbnail_url,
                "weekdays": [],
                "is_finished": wt.is_finished,
                "is_paused": wt.is_paused,
                "is_new": wt.is_new,
                "is_adult": wt.is_adult,
                "author_summary": ", ".join(wt.writer_names),
                "status": wt.status,
                "genres": wt.genres,
                "tags": wt.tags,
                "has_new_episode": wt.latest_episode_no > wt.last_downloaded_no > 0,
            }
        )

    return result


@router.post("/naver-list/{title_id}/subscribe")
async def naver_list_subscribe(title_id: str, payload: NaverListEntryIn):
    if not await asyncio.to_thread(repository.exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new,
            title_id,
            payload.title,
            False,
            None,
            repository.SOURCE_MANUAL,
            payload.thumbnail_url,
        )
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/naver-list/{title_id}/exclude")
async def naver_list_exclude(title_id: str, payload: NaverListEntryIn):
    if not await asyncio.to_thread(repository.exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new,
            title_id,
            payload.title,
            False,
            None,
            repository.SOURCE_MANUAL,
            payload.thumbnail_url,
        )
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_EXCLUDED)
    _trigger_enrich(title_id, register_authors_enabled=False)  # 제외해도 정보는 채우되, 저자를 관심작가로 올리진 않음
    return _to_out(await asyncio.to_thread(repository.get, title_id))


# ── 작가/태그 자동추가 레지스트리 ──────────────────────────────────

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


@router.get("/authors/interested", response_model=list[InterestedAuthorOut])
async def list_interested_authors():
    """
    "등록된 작가" = watched_authors에서 enabled=1 / "전체 작가 목록" = enabled=0.
    이름은 watched_authors에 있으면 그걸 쓰고, 비어있으면 DB의 웹툰 어딘가에
    저장된 실제 이름으로 보정한다 (예전에 이름 없이 등록됐던 것도 여기서 채워짐).
    """
    watched = await asyncio.to_thread(repository.list_watched_authors)
    all_pairs = await asyncio.to_thread(repository.list_all_writer_id_name_pairs)

    result_map: dict[str, InterestedAuthorOut] = {}
    for a in watched:
        name = a.author_name or all_pairs.get(a.author_id, "")
        result_map[a.author_id] = InterestedAuthorOut(author_id=a.author_id, author_name=name, enabled=a.enabled)

    # watched_authors에 아직 한 번도 안 들어간 저자(웹툰 데이터에만 있는 경우)는
    # "전체 작가 목록"(미등록) 쪽에 기본으로 채운다.
    for author_id, author_name in all_pairs.items():
        if author_id not in result_map:
            result_map[author_id] = InterestedAuthorOut(author_id=author_id, author_name=author_name, enabled=False)

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


# ── 카카오웹툰 작가 (네이버와 인터페이스는 같지만, 작가에 고유 ID가 없어서
#    이름 문자열 자체를 author_id로 쓴다 — 실제 API 응답 3곳에서 확인된 제약) ──

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
    """네이버의 /authors/candidates와 동일한 역할 — 요일 7개+신작+완결을 통째로 훑어서
    이름 후보를 즉시 뽑는다. 카탈로그가 커서(2000개 이상) 몇 초 걸릴 수 있다."""
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        items = await kakao_api.fetch_full_catalog(session, settings.request_timeout_seconds)
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
    """지금 추적 중인 모든 웹툰을 훑어서 작가/태그 레지스트리를 즉시 채운다."""
    async def _run():
        settings = get_settings()
        job_status.start("registry")
        job_status.log_line("registry", "작가/태그 재동기화 시작")
        try:
            async with aiohttp.ClientSession() as session:
                count = await tracker.resync_registry(session, settings)
            job_status.log_line("registry", f"{count}개 웹툰 처리 완료")
            job_status.finish("registry", success=True)
        except Exception as e:
            job_status.log_line("registry", f"오류: {e}")
            job_status.finish("registry", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


@router.post("/metadata/sync")
async def sync_metadata():
    """추적 중인 웹툰 폴더를 스캔해서 info.xml/cover.jpg 누락분만 다시 만든다."""
    async def _run():
        settings = get_settings()
        job_status.start("metadata_sync")
        job_status.log_line("metadata_sync", "메타 동기화 시작")
        try:
            count = await tracker.sync_metadata_for_all(settings)
            job_status.log_line("metadata_sync", f"{count}개 웹툰 정리 완료")
            job_status.finish("metadata_sync", success=True)
        except Exception as e:
            job_status.log_line("metadata_sync", f"오류: {e}")
            job_status.finish("metadata_sync", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


class WebtoonServerUrlOut(BaseModel):
    webtoon_server_url: str


class WebtoonServerUrlIn(BaseModel):
    webtoon_server_url: str


@router.get("/settings/webtoon-server", response_model=WebtoonServerUrlOut)
async def get_webtoon_server_url():
    """리포트에 '바로가기' 링크를 붙일 때 조회할 별도 웹툰 뷰어 서버 주소.
    비워두면 링크 없이 제목만 나열한다."""
    value = await asyncio.to_thread(repository.get_setting, "webtoon_server_url")
    return WebtoonServerUrlOut(webtoon_server_url=value or "")


@router.post("/settings/webtoon-server", response_model=WebtoonServerUrlOut)
async def set_webtoon_server_url(payload: WebtoonServerUrlIn):
    await asyncio.to_thread(
        repository.set_setting, "webtoon_server_url", payload.webtoon_server_url.strip() or None
    )
    return await get_webtoon_server_url()


@router.get("/webtoon-server/lookup")
async def lookup_webtoon_server_reader_url(title: str):
    """구독중인 웹툰 카드의 '뷰어에서 보기' 아이콘이 누르는 순간 호출한다 — 매번 최신
    상태를 물어보는 게 목적이라, 캐시하지 않고 그때그때 webtoon-server에 직접 조회한다."""
    server_url = await asyncio.to_thread(repository.get_setting, "webtoon_server_url")
    if not server_url:
        return {"url": None}
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        url = await webtoon_server_client.fetch_reader_url(session, server_url, title, settings.request_timeout_seconds)
    return {"url": url}


@router.post("/jobs/report/run")
async def run_report_job_now():
    asyncio.create_task(scheduler_mod.run_report_job())
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


# ── 수동 다운로드 ──────────────────────────────────────────────────

class ManualAnalyzeEpisodeOut(BaseModel):
    episode_no: int
    subtitle: str
    owned: bool
    is_locked: bool


class ManualAnalyzeOut(BaseModel):
    title_id: str
    title: str
    episodes: list[ManualAnalyzeEpisodeOut]


class ManualDownloadIn(BaseModel):
    title_id: str
    episode_nos: list[int]

    @field_validator("episode_nos")
    @classmethod
    def not_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("다운로드할 회차를 하나 이상 선택해주세요.")
        return v


@router.get("/manual-download/search")
async def manual_download_search_title(query: str):
    """titleId를 모를 때 제목/작가로 후보를 찾는다 (네이버 통합검색 — 장기휴재작도 나옴)."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="검색할 제목을 입력해주세요.")
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        results = await naver_api.search_webtoons(session, query.strip(), settings.request_timeout_seconds)
    return [
        {"title_id": item.title_id, "title": item.title_name, "thumbnail_url": item.thumbnail_url}
        for item in results[:10]
    ]


@router.get("/manual-download/analyze", response_model=ManualAnalyzeOut)
async def manual_download_analyze(title_id: str):
    if not title_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="titleId는 숫자만 입력할 수 있습니다.")
    settings = get_settings()
    info, rows = await manual_download.analyze(title_id, settings)
    if info is None:
        raise HTTPException(status_code=400, detail="해당 titleId 정보를 네이버에서 찾지 못했습니다.")
    return ManualAnalyzeOut(
        title_id=title_id,
        title=info.title_name,
        episodes=[
            ManualAnalyzeEpisodeOut(
                episode_no=r.episode_no, subtitle=r.subtitle, owned=r.owned, is_locked=r.is_locked
            )
            for r in rows
        ],
    )


@router.post("/manual-download/run")
async def manual_download_run(payload: ManualDownloadIn):
    settings = get_settings()
    asyncio.create_task(manual_download.download_selected(payload.title_id, payload.episode_nos, settings))
    return {"status": "started"}


# ── 설정 (잡별 스케줄: 끄기 / N분마다 / 특정 요일·시각) ────────────────

class JobScheduleIn(BaseModel):
    mode: str  # off | interval | cron
    interval_minutes: int = 60
    cron_hour: int = 3
    cron_minute: int = 0
    cron_days: list[str] = []

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        if v not in schedule_config.VALID_MODES:
            raise ValueError(f"mode는 {schedule_config.VALID_MODES} 중 하나여야 합니다.")
        return v

    @field_validator("interval_minutes")
    @classmethod
    def interval_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("주기는 1분 이상이어야 합니다.")
        return v

    @field_validator("cron_hour")
    @classmethod
    def hour_in_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("시(hour)는 0~23이어야 합니다.")
        return v

    @field_validator("cron_minute")
    @classmethod
    def minute_in_range(cls, v: int) -> int:
        if not (0 <= v <= 59):
            raise ValueError("분(minute)은 0~59여야 합니다.")
        return v

    @field_validator("cron_days")
    @classmethod
    def days_must_be_valid(cls, v: list[str]) -> list[str]:
        invalid = [d for d in v if d not in schedule_config.VALID_DAYS]
        if invalid:
            raise ValueError(f"알 수 없는 요일: {invalid}")
        return v


class SchedulesIn(BaseModel):
    discovery_job: JobScheduleIn
    download_job: JobScheduleIn
    report_job: JobScheduleIn


def _schedule_to_dict(job_id: str) -> dict:
    s = schedule_config.get_schedule(job_id, scheduler_mod.DEFAULT_SCHEDULES[job_id])
    return {
        "mode": s.mode,
        "interval_minutes": s.interval_minutes,
        "cron_hour": s.cron_hour,
        "cron_minute": s.cron_minute,
        "cron_days": s.cron_days,
    }


@router.get("/settings")
async def get_schedules():
    return await asyncio.to_thread(
        lambda: {job_id: _schedule_to_dict(job_id) for job_id in scheduler_mod.DEFAULT_SCHEDULES}
    )


@router.post("/settings")
async def update_schedules(payload: SchedulesIn, request: Request):
    for job_id, job_in in payload.model_dump().items():
        job_schedule = schedule_config.JobSchedule(**job_in)
        await asyncio.to_thread(schedule_config.set_schedule, job_id, job_schedule)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await asyncio.to_thread(scheduler_mod.reschedule_all, scheduler)

    return await get_schedules()


# ── 디스코드 설정 + 테스트 ─────────────────────────────────────────

class DiscordSettingsOut(BaseModel):
    webhook_url_set: bool
    bot_token_set: bool
    notify_channel_id: str
    bot_ready: bool


class DiscordSettingsIn(BaseModel):
    webhook_url: str = ""
    bot_token: str = ""  # 비워두면 기존 값 유지
    notify_channel_id: str = ""


@router.get("/settings/discord", response_model=DiscordSettingsOut)
async def get_discord_settings():
    return DiscordSettingsOut(
        webhook_url_set=bool(await asyncio.to_thread(discord_config.get_webhook_url)),
        bot_token_set=bool(await asyncio.to_thread(discord_config.get_bot_token)),
        notify_channel_id=await asyncio.to_thread(discord_config.get_notify_channel_id),
        bot_ready=discord_bot.is_ready(),
    )


@router.post("/settings/discord", response_model=DiscordSettingsOut)
async def update_discord_settings(payload: DiscordSettingsIn):
    await asyncio.to_thread(discord_config.set_webhook_url, payload.webhook_url)
    await asyncio.to_thread(discord_config.set_bot_token, payload.bot_token)
    await asyncio.to_thread(discord_config.set_notify_channel_id, payload.notify_channel_id)
    await discord_bot.restart_bot()
    return await get_discord_settings()


@router.post("/settings/discord/test-webhook")
async def test_discord_webhook():
    success, message = await discord_notify.send_test_webhook_message()
    return {"success": success, "message": message}


@router.post("/settings/discord/test-bot")
async def test_discord_bot():
    success, message = await discord_bot.send_test_message()
    return {"success": success, "message": message}


# ── 백업 / 복원 ────────────────────────────────────────────────────

@router.get("/backup")
async def download_backup():
    return await asyncio.to_thread(repository.export_all)


@router.post("/restore")
async def restore_backup(data: dict):
    try:
        await asyncio.to_thread(repository.restore_all, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 스키마가 안 맞는 백업(필수 컬럼 없음 등)은 DB 예외가 그대로 올라올 수 있다 —
        # 원문 그대로 500으로 흘리는 대신 "복원 실패"로 명확히 감싼다. 트랜잭션은
        # write_transaction이 이미 롤백했으므로 DB는 이전 상태 그대로 안전하다.
        raise HTTPException(status_code=400, detail=f"백업 파일 형식이 올바르지 않아 복원하지 못했습니다: {e}")
    return {"status": "restored"}


# ── 수동 실행 + 진행상황 ──────────────────────────────────────────

@router.get("/jobs/status")
async def jobs_status():
    return await asyncio.to_thread(job_status.snapshot)


@router.get("/jobs/history")
async def jobs_history(limit_per_job: int = 10):
    """스케줄대로 자동 실행된 잡이 실제로 돌았는지/성공했는지 나중에 확인할 수 있는 이력."""
    return await asyncio.to_thread(repository.list_job_history, limit_per_job)


@router.delete("/jobs/history/{entry_id}")
async def delete_job_history_entry(entry_id: int):
    await asyncio.to_thread(repository.delete_job_history_entry, entry_id)
    return {"status": "deleted"}


@router.delete("/jobs/history")
async def clear_all_job_history():
    await asyncio.to_thread(repository.clear_job_history)
    return {"status": "cleared"}


# ── 회차 단위 다운로드 이력 ──────────────────────────────────────────

class EpisodeHistoryOut(BaseModel):
    id: int
    title_id: str
    title_name: str
    episode_no: int
    subtitle: str
    status: str
    error_msg: str
    downloaded_at: str


class EpisodeHistoryPageOut(BaseModel):
    items: list[EpisodeHistoryOut]
    total: int
    page: int
    page_size: int


@router.get("/episode-history", response_model=EpisodeHistoryPageOut)
async def get_episode_history(status: str | None = None, search: str = "", page: int = 1):
    if status and status not in ("success", "failed"):
        raise HTTPException(status_code=400, detail="status는 success/failed 중 하나여야 합니다.")
    if page < 1:
        raise HTTPException(status_code=400, detail="page는 1 이상이어야 합니다.")
    page_size = 30
    rows, total = await asyncio.to_thread(repository.list_episode_history, status, search, page, page_size)
    return EpisodeHistoryPageOut(items=rows, total=total, page=page, page_size=page_size)


@router.delete("/episode-history/{entry_id}")
async def delete_episode_history_entry(entry_id: int):
    await asyncio.to_thread(repository.delete_episode_history, entry_id)
    return {"status": "deleted"}


@router.delete("/episode-history")
async def clear_all_episode_history():
    """이력만 지운다 — 실제로 받은 파일은 그대로 유지된다."""
    await asyncio.to_thread(repository.clear_episode_history)
    return {"status": "cleared"}


_KEY_EPISODE_HISTORY_RETENTION_DAYS = "episode_history_retention_days"


class RetentionDaysOut(BaseModel):
    retention_days: int  # 0 = 자동삭제 끔


class RetentionDaysIn(BaseModel):
    retention_days: int

    @field_validator("retention_days")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("보관 기간은 0 이상이어야 합니다 (0 = 자동삭제 끔).")
        return v


_KEY_JOB_HISTORY_RETENTION_DAYS = "job_history_retention_days"


@router.get("/jobs/history/retention-days", response_model=RetentionDaysOut)
async def get_job_history_retention_days():
    value = await asyncio.to_thread(repository.get_setting, _KEY_JOB_HISTORY_RETENTION_DAYS)
    return RetentionDaysOut(retention_days=int(value) if value else 0)


@router.post("/jobs/history/retention-days", response_model=RetentionDaysOut)
async def set_job_history_retention_days(payload: RetentionDaysIn):
    await asyncio.to_thread(
        repository.set_setting, _KEY_JOB_HISTORY_RETENTION_DAYS, str(payload.retention_days) if payload.retention_days > 0 else None
    )
    return await get_job_history_retention_days()


@router.get("/episode-history/retention-days", response_model=RetentionDaysOut)
async def get_retention_days():
    value = await asyncio.to_thread(repository.get_setting, _KEY_EPISODE_HISTORY_RETENTION_DAYS)
    return RetentionDaysOut(retention_days=int(value) if value else 0)


@router.post("/episode-history/retention-days", response_model=RetentionDaysOut)
async def set_retention_days(payload: RetentionDaysIn):
    await asyncio.to_thread(
        repository.set_setting,
        _KEY_EPISODE_HISTORY_RETENTION_DAYS,
        str(payload.retention_days) if payload.retention_days > 0 else None,
    )
    return await get_retention_days()


@router.post("/jobs/discovery/run")
async def trigger_discovery_job():
    asyncio.create_task(scheduler_mod.run_discovery_job())
    return {"status": "started"}


@router.post("/jobs/download/run")
async def trigger_download_job():
    asyncio.create_task(scheduler_mod.run_download_job())
    return {"status": "started"}
