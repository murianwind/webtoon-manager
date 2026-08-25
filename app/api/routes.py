"""
웹 페이지가 사용하는 REST API.

LAN 전용, 인증 없음. 입력값 검증 실패 시 크래시 대신 명확한 4xx 응답을 준다.

- 구독중/구독해제/제외됨 조회 및 상태 전환 : /webtoons/*
- 네이버 전체 웹툰 목록 조회 + 거기서 바로 구독/제외 : /naver-list/*
- 다운로드/스캔 주기 설정 : /settings
- 수동 실행 + 진행상황 조회 : /jobs/*
"""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import job_status, naver_api, repository, schedule_config
from app import scheduler as scheduler_mod
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
    )


def _get_or_404(title_id: str):
    wt = repository.get(title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="해당 titleId를 목록에서 찾을 수 없습니다.")
    return wt


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
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unsubscribe", response_model=WebtoonOut)
async def unsubscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


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
    async with aiohttp.ClientSession() as session:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)

    existing = await asyncio.to_thread(repository.list_all)
    existing_status = {w.title_id: w.status for w in existing}

    return [
        {
            "title_id": item.title_id,
            "title": item.title_name,
            "thumbnail_url": item.thumbnail_url,
            "weekdays": item.weekdays,
            "is_finished": item.is_finished,
            "author_summary": item.author_summary,
            "status": existing_status.get(item.title_id),
        }
        for item in items
    ]


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
    return _to_out(await asyncio.to_thread(repository.get, title_id))


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
    commands_job: JobScheduleIn


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



# ── 수동 실행 + 진행상황 ──────────────────────────────────────────

@router.get("/jobs/status")
async def jobs_status():
    return await asyncio.to_thread(job_status.snapshot)


@router.post("/jobs/discovery/run")
async def trigger_discovery_job():
    asyncio.create_task(scheduler_mod.run_discovery_job())
    return {"status": "started"}


@router.post("/jobs/download/run")
async def trigger_download_job():
    asyncio.create_task(scheduler_mod.run_download_job())
    return {"status": "started"}
