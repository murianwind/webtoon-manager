"""네이버 웹툰: 구독중/구독해제/제외됨 조회·전환, 전체 웹툰 목록에서 바로 구독/목록제외."""


import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from app import (
    daily_plus,
    naver_api,
    naver_list_service,
    repository,
    subscription_history,
    tracker,
    weekdays,
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
    weekdays: list[str] = []  # 연재 요일(mon~sun). 전체목록을 불러올 때 저장해 둔 값 — 요일별 목록에 없는 작품(완결/장기 휴재)은 비어 있다


def _to_out(wt, days: list[str] | None = None) -> WebtoonOut:
    """days: 전체목록/작품 페이지에서 알아낸 연재 요일(없으면 [] — 화면에서 "기타")."""
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
        weekdays=days or [],
    )


def _get_or_404(title_id: str):
    wt = repository.get(title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="해당 titleId를 목록에서 찾을 수 없습니다.")
    return wt


def _trigger_enrich(title_id: str, register_authors_enabled: bool = True, record_history: bool = False) -> None:
    """
    백그라운드에서 정보/작가등록을 바로 채운다 (다음 정기 스캔까지 기다리지 않음).
    register_authors_enabled=False로 부르면(목록제외/구독해제 시) 이 웹툰의 정보는
    채우되, 그 저자를 "등록된 작가"(자동 신작추가 대상)로 올리지는 않는다 —
    제외한 웹툰의 저자가 엉뚱하게 관심작가로 등록되면 안 되기 때문이다.
    record_history=True면(구독할 때) 그 결과를 실행 이력에 남긴다.
    """
    async def _run():
        if record_history:
            await _enrich_and_record(title_id, register_authors_enabled)
            return
        async with aiohttp.ClientSession() as session:
            await tracker.enrich_one(
                session, title_id, get_settings(), register_authors_enabled=register_authors_enabled
            )

    asyncio.create_task(_run())


async def _enrich_and_record(title_id: str, register_authors_enabled: bool) -> None:
    """구독 직후 정보/작가를 채우고, 구독했다는 사실과 작가 등록 결과를 실행 이력에 한 건으로 남긴다."""
    row = await asyncio.to_thread(repository.get, title_id)
    title = row.title if row else title_id
    report: list[str] = []
    try:
        async with aiohttp.ClientSession() as session:
            success, message = await tracker.enrich_one(
                session, title_id, get_settings(), register_authors_enabled=register_authors_enabled, report=report
            )
    except Exception as e:
        log.error("구독 후 정보/작가 등록 중 예외 (titleId=%s): %s", title_id, e)
        success, message = False, f"작가 등록 중 오류: {e}"
    await asyncio.to_thread(subscription_history.record, "네이버", title, report or [message], success)


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
    hidden = await daily_plus.current_ids(get_settings().request_timeout_seconds)
    rows = [r for r in rows if r.title_id not in hidden]  # 매일+ 작품은 목록에 보이지 않는다
    if status == repository.STATUS_UNSUBSCRIBED:
        # "구독해제" 탭엔 실제로 구독했다가 해제한 것만 있어야 한다 — 구독한 적 없는
        # 작품은 이 상태를 거칠 방법이 이제 없지만(목록으로가 대신 전용 상태로 보냄),
        # 예전에 잘못 들어간 기록이 남아있을 수 있어서 한 번 더 여기서도 막아둔다.
        rows = [r for r in rows if r.ever_subscribed]
    days_by_id = await asyncio.to_thread(weekdays.get_naver_map)  # 전체목록을 불러올 때 저장해 둔 요일(없으면 [] — 화면에서 "기타")
    return [_to_out(r, days_by_id.get(r.title_id)) for r in rows]


@router.post("/webtoons/{title_id}/subscribe", response_model=WebtoonOut)
async def subscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(
        title_id, register_authors_enabled=await asyncio.to_thread(_is_author_auto_register_enabled), record_history=True
    )
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
    """전체목록(네이버) — 만드는 일은 naver_list_service가 한다."""
    try:
        return await naver_list_service.build_naver_list(get_settings())
    except naver_api.NaverApiError as e:
        raise HTTPException(status_code=502, detail=f"네이버 웹툰 목록을 불러오지 못했습니다: {e}") from e


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
    _trigger_enrich(
        title_id, register_authors_enabled=await asyncio.to_thread(_is_author_auto_register_enabled), record_history=True
    )
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
    tracked = await asyncio.to_thread(repository.get, title_id)
    return _render_exclude_confirm_html(
        title_id, title, f"/api/naver-list/{title_id}/exclude",
        {"title": title or title_id, "thumbnail_url": thumbnail_url},
        already_excluded=tracked is not None and tracked.status == repository.STATUS_EXCLUDED,  # 이미 제외된 작품이면 안내만(버튼 없음)
    )
