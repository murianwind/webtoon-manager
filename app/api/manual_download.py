"""네이버 수동 다운로드(검색/분석/받기)."""


import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app import (
    daily_plus,
    manual_download,
    naver_api,
    repository,
)
from app.config import get_settings


log = logging.getLogger(__name__)

router = APIRouter()



class ManualAnalyzeEpisodeOut(BaseModel):
    episode_no: int
    subtitle: str
    owned: bool
    is_locked: bool


class ManualAnalyzeOut(BaseModel):
    title_id: str
    title: str
    thumbnail_url: str = ""  # 화면에서 구독할 때 같이 보낸다
    subscription: str | None = None  # 이 프로그램의 구독 상태(active 등, 등록 안 했으면 None)
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
        hidden = await daily_plus.current_ids(settings.request_timeout_seconds, session)
    results = [item for item in results if item.title_id not in hidden]  # 매일+ 작품은 검색되지 않는다
    # 한 번에 읽어서 카드마다 DB를 따로 부르지 않는다 — 카드가 이미 구독한 작품을 "구독"으로 보여주지 않게
    status_by_id = {wt.title_id: wt.status for wt in await asyncio.to_thread(repository.list_all)}
    return [
        {
            "title_id": item.title_id, "title": item.title_name, "thumbnail_url": item.thumbnail_url,
            "subscription": status_by_id.get(item.title_id),
        }
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
    tracked = await asyncio.to_thread(repository.get, title_id)
    return ManualAnalyzeOut(
        title_id=title_id,
        title=info.title_name,
        thumbnail_url=info.thumbnail_url,
        subscription=tracked.status if tracked else None,
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
