"""잡별 실행 스케줄 설정(끄기 / N분마다 / 특정 요일·시각, 다운로드는 스케줄 여러 개)."""


import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import (
    schedule_config,
)
from app import scheduler as scheduler_mod


log = logging.getLogger(__name__)

router = APIRouter()



class CronTimeIn(BaseModel):
    hour: int
    minute: int

    @field_validator("hour")
    @classmethod
    def hour_in_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("시(hour)는 0~23이어야 합니다.")
        return v

    @field_validator("minute")
    @classmethod
    def minute_in_range(cls, v: int) -> int:
        if not (0 <= v <= 59):
            raise ValueError("분(minute)은 0~59여야 합니다.")
        return v


class JobScheduleIn(BaseModel):
    mode: str  # off | interval | cron
    interval_minutes: int = 60
    cron_times: list[CronTimeIn] = [CronTimeIn(hour=3, minute=0)]
    cron_days: list[str] = []
    target: str = "naver"  # 다운로드 스케줄에서만 쓴다: naver | kakao | both

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        if v not in schedule_config.VALID_MODES:
            raise ValueError(f"mode는 {schedule_config.VALID_MODES} 중 하나여야 합니다.")
        return v

    @field_validator("target")
    @classmethod
    def target_must_be_valid(cls, v: str) -> str:
        if v not in schedule_config.VALID_TARGETS:
            raise ValueError(f"대상은 {schedule_config.VALID_TARGETS} 중 하나여야 합니다.")
        return v

    @field_validator("interval_minutes")
    @classmethod
    def interval_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("주기는 1분 이상이어야 합니다.")
        return v

    @field_validator("cron_times")
    @classmethod
    def times_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("cron 모드는 시각을 최소 1개 지정해야 합니다.")
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
    download_job: list[JobScheduleIn]  # 다운로드는 스케줄을 여러 개 등록할 수 있다(각각 대상이 다름)
    report_job: JobScheduleIn
    archive_job: JobScheduleIn


def _schedule_dict(s: "schedule_config.JobSchedule") -> dict:
    return {
        "mode": s.mode,
        "interval_minutes": s.interval_minutes,
        "cron_times": s.cron_times,
        "cron_days": s.cron_days,
        "target": s.target,
    }


def _schedule_to_dict(job_id: str) -> dict | list[dict]:
    if job_id == "download_job":
        return [_schedule_dict(s) for s in schedule_config.get_download_schedules(scheduler_mod.DEFAULT_SCHEDULES[job_id])]
    return _schedule_dict(schedule_config.get_schedule(job_id, scheduler_mod.DEFAULT_SCHEDULES[job_id]))


def _validate_archive_schedule_gap(payload: "SchedulesIn") -> None:
    """아카이빙이 다운로드 도중 파일을 옮기다 겹치는 걸 막기 위해, 둘 다 '특정 시각'
    모드일 때는 아카이빙의 모든 지정 시각이 다운로드의 모든 지정 시각보다 최소 10분
    뒤여야 한다(둘 다 여러 시각을 가질 수 있어서, 모든 조합을 확인한다). 다운로드가
    '몇 분마다' 모드면(계속 도니 안전한 간격을 이 방식으로 보장할 수 없어서) 이 검증은
    건너뛴다."""
    archive_in = payload.archive_job
    if archive_in.mode != "cron":
        return
    for download_in in payload.download_job:  # 다운로드 스케줄이 여러 개일 수 있어서 전부 확인한다
        if download_in.mode != "cron":
            continue
        _check_archive_gap_against(archive_in, download_in)


def _check_archive_gap_against(archive_in: "JobScheduleIn", download_in: "JobScheduleIn") -> None:
    for download_time in download_in.cron_times:
        download_minutes = download_time.hour * 60 + download_time.minute
        for archive_time in archive_in.cron_times:
            archive_minutes = archive_time.hour * 60 + archive_time.minute
            gap = (archive_minutes - download_minutes) % (24 * 60)
            if gap < 10:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"아카이빙 시각({archive_time.hour:02d}:{archive_time.minute:02d})은 "
                        f"다운로드 시각({download_time.hour:02d}:{download_time.minute:02d})보다 "
                        "최소 10분 뒤여야 합니다 (다운로드 도중 파일이 옮겨지는 걸 방지)."
                    ),
                )


@router.get("/settings")
async def get_schedules():
    return await asyncio.to_thread(
        lambda: {job_id: _schedule_to_dict(job_id) for job_id in scheduler_mod.DEFAULT_SCHEDULES}
    )


@router.post("/settings")
async def update_schedules(payload: SchedulesIn, request: Request):
    _validate_archive_schedule_gap(payload)
    for job_id, job_in in payload.model_dump().items():
        if job_id == "download_job":
            await asyncio.to_thread(
                schedule_config.set_download_schedules, [schedule_config.JobSchedule(**entry) for entry in job_in]
            )
        else:
            await asyncio.to_thread(schedule_config.set_schedule, job_id, schedule_config.JobSchedule(**job_in))

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await asyncio.to_thread(scheduler_mod.reschedule_all, scheduler)

    return await get_schedules()
