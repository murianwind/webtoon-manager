"""진단 — 화면의 "기타" 탭이 왜 그렇게 나오는지 원인별로 알려 준다(HAR 없이 확인)."""
import asyncio

from fastapi import APIRouter, HTTPException

from app import repository, weekday_diagnostics

router = APIRouter()


@router.get("/diagnostics/weekday-gaps")
async def weekday_gaps(status: str | None = None):
    """요일을 알 수 없어 "기타"에 남은 작품을 원인별로 센다. status: active/unsubscribed/excluded(없으면 전부)."""
    if status and status not in (repository.STATUS_ACTIVE, repository.STATUS_UNSUBSCRIBED, repository.STATUS_EXCLUDED):
        raise HTTPException(status_code=400, detail="status는 active/unsubscribed/excluded 중 하나여야 합니다.")
    return await asyncio.to_thread(weekday_diagnostics.summarize, status or None)
