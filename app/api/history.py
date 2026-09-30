"""회차 단위 다운로드 이력."""


import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import (
    repository,
)

from app.api.common import (
    RetentionDaysIn,
    RetentionDaysOut,
)


log = logging.getLogger(__name__)

router = APIRouter()



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
