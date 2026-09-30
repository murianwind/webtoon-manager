"""네이버 웹툰: 구독중/구독해제/제외됨 조회·전환, 전체 웹툰 목록에서 바로 구독/목록제외."""


import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from app import (
    naver_api,
    repository,
    tracker,
)
from app import archiver
from app.config import get_settings

from app.api.common import (
    _is_author_auto_register_enabled,
    _render_exclude_confirm_html,
)


log = logging.getLogger(__name__)

router = APIRouter()



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
    ever_subscribed: bool


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
        has_new_episode=wt.has_update,
        writer_ids=wt.writer_ids,
        writer_names=wt.writer_names,
        ever_subscribed=wt.ever_subscribed,
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
    if status == repository.STATUS_UNSUBSCRIBED:
        # "구독해제" 탭엔 실제로 구독했다가 해제한 것만 있어야 한다 — 구독한 적 없는
        # 작품은 이 상태를 거칠 방법이 이제 없지만(목록으로가 대신 전용 상태로 보냄),
        # 예전에 잘못 들어간 기록이 남아있을 수 있어서 한 번 더 여기서도 막아둔다.
        rows = [r for r in rows if r.ever_subscribed]
    return [_to_out(r) for r in rows]


@router.post("/webtoons/{title_id}/subscribe", response_model=WebtoonOut)
async def subscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(title_id, register_authors_enabled=await asyncio.to_thread(_is_author_auto_register_enabled))
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unsubscribe", response_model=WebtoonOut)
async def unsubscribe(title_id: str):
    wt = await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)

    # 즉시 옮기지 않고 대기열에만 넣는다 — 실제 이동은 다음 아카이빙 스케줄이 돌 때(run_archive_job) 같이 처리된다.
    # 다운로드 도중과 안 겹치게 하는 기존 안전장치(10분 간격 검증 등)를 이 트리거도 그대로 타게 하기 위함.
    await asyncio.to_thread(archiver.queue_finish_archive_if_applicable, title_id, wt.is_finished)

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


@router.get("/webtoons/history-only", response_model=list[WebtoonOut])
async def list_history_only_webtoons():
    """구독 이력은 있지만(ever_subscribed) 지금 구독 중은 아닌 것들 — "설정 > 구독해제
    관리"에서 잘못 남아있는 이력을 찾아 초기화할 때 쓰는 목록이라, active는 제외한다
    (지금 구독 중인 걸 여기서 잘못 건드리면 안 되므로)."""
    rows = await asyncio.to_thread(repository.list_all)
    rows = [r for r in rows if r.ever_subscribed and r.status != repository.STATUS_ACTIVE]
    return [_to_out(r) for r in rows]


@router.post("/webtoons/{title_id}/register-unsubscribed", response_model=WebtoonOut)
async def register_unsubscribed_webtoon(title_id: str):
    """"설정 > 구독해제 관리"에서 titleId만 입력해서 등록하는 기능 — 이전 버그로
    완전히 사라져버린 작품을 구독 이력이 있는 채로 "구독해제" 탭에 복구할 때 쓴다.
    이미 구독 중(active)인 작품은 이 방식으로 억지로 옮기면 안 되므로 막는다."""
    existing = await asyncio.to_thread(repository.get, title_id)
    if existing is not None and existing.status == repository.STATUS_ACTIVE:
        raise HTTPException(status_code=400, detail="이미 구독 중인 웹툰은 이 방식으로 등록할 수 없습니다.")

    if existing is not None:
        title, thumbnail_url = existing.title, existing.thumbnail_url
    else:
        settings = get_settings()
        async with aiohttp.ClientSession() as session:
            info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
        if info is None:
            raise HTTPException(status_code=404, detail="네이버에서 이 titleId를 찾을 수 없습니다.")
        title, thumbnail_url = info.title_name, info.thumbnail_url

    await asyncio.to_thread(repository.register_as_unsubscribed_history, title_id, title, thumbnail_url)
    _trigger_enrich(title_id, register_authors_enabled=False)  # 장르/작가 등 정보만 채우고, 작가를 관심작가로 자동등록하진 않음
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unregister", response_model=WebtoonOut)
async def unregister_webtoon(title_id: str):
    """"목록으로" — 구독 이력이 있는 작품 전용. 완전 삭제 대신 이 전용 상태로 옮겨서
    DB 기록(과 ever_subscribed 이력)을 남긴다 — 구독해제/제외됨 어느 탭에도 안 뜨고,
    네이버 전체목록에서는(완결/휴재라 요일별 목록엔 없어도) 계속 보인다. 구독 이력이
    없는 작품은 이 상태를 쓸 이유가 없으니(그런 건 완전 삭제로 충분) 400으로 막는다."""
    wt = await asyncio.to_thread(_get_or_404, title_id)
    if not wt.ever_subscribed:
        raise HTTPException(status_code=400, detail="구독한 적 없는 작품은 완전 삭제를 사용하세요.")
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNREGISTERED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.delete("/webtoons/{title_id}")
async def delete_webtoon_permanently(title_id: str):
    """구독해제/제외됨 탭에서 "목록으로"(완결작이면 별도 "완전 삭제")로 DB 기록을
    완전히 지운다 — 구독 중(active)인 것만 막는다. 실수로 지금 받고 있는 작품을
    지우는 사고를 막는 게 목적이라, 구독을 안 하고 있는 상태(구독해제/제외됨)는
    둘 다 똑같이 안전하게 완전 삭제할 수 있다."""
    webtoon = await asyncio.to_thread(_get_or_404, title_id)
    if webtoon.status == repository.STATUS_ACTIVE:
        raise HTTPException(status_code=400, detail="구독 중인 웹툰은 완전 삭제할 수 없습니다.")
    await asyncio.to_thread(repository.hard_delete, title_id)
    return {"status": "deleted"}


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
        if tracked is not None and tracked.has_update != item.has_update:
            # 이 목록을 훑는 김에, 추적 중인 작품의 실제 네이버 up 값을 DB에
            # 갱신해둔다 — 개별 작품 API엔 이 값이 없어서, 다른 탭(구독해제/제외됨
            # 등)에서 항상 최신값을 보여주려면 여기서 갱신하는 방법뿐이다.
            await asyncio.to_thread(repository.update_has_update, item.title_id, item.has_update)
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
                "ever_subscribed": tracked.ever_subscribed if tracked else False,
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
                "ever_subscribed": wt.ever_subscribed,
                "genres": wt.genres,
                "tags": wt.tags,
                "has_new_episode": wt.has_update,
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
    _trigger_enrich(title_id, register_authors_enabled=await asyncio.to_thread(_is_author_auto_register_enabled))
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
            False,  # mark_ever_subscribed: 미등록 상태에서 바로 제외하는 거라 실제 구독은 아니었음
        )
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_EXCLUDED)
    _trigger_enrich(title_id, register_authors_enabled=False)  # 제외해도 정보는 채우되, 저자를 관심작가로 올리진 않음
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.get("/webtoons/{title_id}/exclude-confirm", response_class=HTMLResponse)
async def exclude_confirm_page(title_id: str, title: str = "", thumbnail_url: str = ""):
    return _render_exclude_confirm_html(
        title_id, title, f"/api/naver-list/{title_id}/exclude",
        {"title": title or title_id, "thumbnail_url": thumbnail_url},
    )
