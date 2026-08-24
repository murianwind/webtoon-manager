"""
구독관리 웹페이지가 사용하는 REST API.

LAN 전용, 인증 없음 (사용자 결정사항). 입력값 검증 실패 시 크래시 대신
명확한 4xx 응답을 준다 (요구사항: 잘못된 입력에 대한 에러 전달).
"""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app import naver_api, repository
from app.config import get_settings
from app.scheduler import run_discovery_job, run_download_job

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


class AddWebtoonIn(BaseModel):
    title_id: str
    title: str | None = None  # 비워두면 네이버 API에서 자동 조회

    @field_validator("title_id")
    @classmethod
    def title_id_must_be_numeric(cls, v: str) -> str:
        if not v.strip().isdigit():
            raise ValueError("titleId는 숫자만 입력할 수 있습니다.")
        return v.strip()


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
    )


@router.get("/webtoons", response_model=list[WebtoonOut])
async def list_webtoons():
    rows = await asyncio.to_thread(repository.list_all)
    return [_to_out(r) for r in rows]


@router.post("/webtoons", response_model=WebtoonOut)
async def add_webtoon(payload: AddWebtoonIn):
    if await asyncio.to_thread(repository.exists, payload.title_id):
        raise HTTPException(status_code=409, detail="이미 목록에 있는 titleId입니다.")

    title = payload.title
    is_adult = False
    if not title:
        settings = get_settings()
        async with aiohttp.ClientSession() as session:
            info = await naver_api.fetch_title_info(session, payload.title_id, settings.request_timeout_seconds)
        if info is None:
            raise HTTPException(
                status_code=400,
                detail="네이버 API에서 해당 titleId 정보를 찾지 못했습니다. title을 직접 입력해주세요.",
            )
        title = info.title_name
        is_adult = info.is_adult

    await asyncio.to_thread(
        repository.upsert_new,
        payload.title_id,
        title,
        is_adult,
        None,
        repository.SOURCE_MANUAL,
    )
    wt = await asyncio.to_thread(repository.get, payload.title_id)
    return _to_out(wt)


def _get_or_404(title_id: str):
    wt = repository.get(title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="해당 titleId를 목록에서 찾을 수 없습니다.")
    return wt


@router.post("/webtoons/{title_id}/subscribe", response_model=WebtoonOut)
async def subscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unsubscribe", response_model=WebtoonOut)
async def unsubscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/exclude", response_model=WebtoonOut)
async def exclude(title_id: str):
    """목록제외: 완전히 추적 대상에서 빼고, 이후 작가/태그 자동추가 대상에서도 영구 제외."""
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_EXCLUDED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


class ImportIdListIn(BaseModel):
    text: str  # 기존 ID_list.txt 내용 그대로 (한 줄당 "제목 titleId")


@router.post("/import/id-list")
async def import_id_list(payload: ImportIdListIn):
    """기존 ID_list.txt 내용을 붙여넣어 한 번에 가져온다. 로컬 스크립트 실행이 필요 없다."""
    imported, skipped = [], []
    for line in payload.text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2 or not parts[1].isdigit():
            skipped.append(line)
            continue
        title, title_id = parts[0], parts[1]
        if await asyncio.to_thread(repository.exists, title_id):
            skipped.append(line)
            continue
        await asyncio.to_thread(
            repository.upsert_new, title_id, title, False, None, repository.SOURCE_MANUAL
        )
        imported.append({"title_id": title_id, "title": title})

    return {"imported": imported, "skipped": skipped}


@router.post("/scan/discovery")
async def trigger_discovery_scan():
    asyncio.create_task(run_discovery_job())
    return {"status": "started"}


@router.post("/scan/download")
async def trigger_download_scan():
    asyncio.create_task(run_download_job())
    return {"status": "started"}
