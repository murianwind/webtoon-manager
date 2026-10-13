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
from app.api.common import metadata_zip_response
from app.config import get_settings
from app.file_utils import remove_forbidden_str


log = logging.getLogger(__name__)

router = APIRouter()



class ManualAnalyzeEpisodeOut(BaseModel):
    episode_no: int
    subtitle: str
    owned: bool
    is_locked: bool
    recorded: bool = False  # 폴더에는 없고 "받은 회차 기록"으로만 받은 것으로 보이는 회차(구독 중인 작품)


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
    tracked = await asyncio.to_thread(repository.get, title_id)
    recorded_up_to = tracked.last_downloaded_no if tracked and tracked.status == repository.STATUS_ACTIVE else 0   # 구독 중인 작품만 "여기까지 받음" 기록을 반영한다
    try:
        info, rows = await manual_download.analyze(title_id, settings, recorded_up_to)
    except naver_api.NaverApiError as e:
        raise HTTPException(status_code=502, detail=f"회차 목록을 불러오지 못했습니다: {e}") from e
    if info is None:
        raise HTTPException(status_code=400, detail="해당 titleId 정보를 네이버에서 찾지 못했습니다.")
    return ManualAnalyzeOut(
        title_id=title_id,
        title=info.title_name,
        thumbnail_url=info.thumbnail_url,
        subscription=tracked.status if tracked else None,
        episodes=[
            ManualAnalyzeEpisodeOut(
                episode_no=r.episode_no, subtitle=r.subtitle, owned=r.owned, is_locked=r.is_locked, recorded=r.recorded
            )
            for r in rows
        ],
    )


@router.post("/manual-download/run")
async def manual_download_run(payload: ManualDownloadIn):
    settings = get_settings()
    asyncio.create_task(manual_download.download_selected(payload.title_id, payload.episode_nos, settings))
    return {"status": "started"}


class ManualRecordIn(BaseModel):
    title_id: str
    episode_nos: list[int]
    downloaded: bool  # True = 받은 것으로 기록, False = 받지 않은 것으로 기록


@router.post("/manual-download/record")
async def manual_download_record(payload: ManualRecordIn):
    """수동 다운로드에서 고른 회차를 "받은 것으로" 또는 "받지 않은 것으로" 기록한다 — 구독 중인 네이버 웹툰만. 네이버는 "여기까지 받음" 한 지점만 기억한다."""
    if not payload.title_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="titleId는 숫자만 입력할 수 있습니다.")
    if not any(n > 0 for n in payload.episode_nos):
        raise HTTPException(status_code=400, detail="기록할 회차를 선택해주세요.")
    try:
        return await manual_download.mark_record(payload.title_id.strip(), payload.episode_nos, payload.downloaded, get_settings())
    except manual_download.ManualRecordError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/manual-download/metadata-zip")
async def manual_download_metadata_zip(title_id: str):
    """분석한 네이버 웹툰의 표지(cover)와 info.xml만 zip 하나로 브라우저에 내려준다. 서버 폴더에는 아무것도 쓰지 않는다."""
    bundle = await manual_download.build_metadata_bundle(title_id, get_settings())
    if bundle is None:
        raise HTTPException(status_code=404, detail="작품 정보를 가져오지 못했습니다 (titleId 확인 필요)")
    return metadata_zip_response(bundle, remove_forbidden_str(bundle.title))

