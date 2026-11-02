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



_DAY_LABELS = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}


class CronTimeIn(BaseModel):
    hour: int
    minute: int
    days: list[str] | None = None  # 이 시각에만 적용하는 요일(비어 있으면 매일). 안 보내면(옛 화면) 스케줄 공통 cron_days를 쓴다

    @field_validator("days")
    @classmethod
    def days_must_be_valid(cls, v: list[str] | None) -> list[str] | None:
        invalid = [d for d in v or [] if d not in schedule_config.VALID_DAYS]
        if invalid:
            raise ValueError(f"알 수 없는 요일: {invalid}")
        return v

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
    cron_days: list[str] = []  # 옛 화면이 보내던 스케줄 공통 요일(읽을 때 요일이 없는 시각에 복사). 새 화면은 시각마다 days를 보낸다
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
    archive_job: JobScheduleIn


def _schedule_dict(s: "schedule_config.JobSchedule") -> dict:
    s = s.sanitized()  # 기본값처럼 정리되지 않은 스케줄도 시각마다 days가 채워진 형태로 내려간다
    return {
        "mode": s.mode,
        "interval_minutes": s.interval_minutes,
        "cron_times": s.cron_times,
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


def _week_minutes(time_in: "CronTimeIn", legacy_days: list[str]) -> list[int]:
    """이 시각이 한 주(월 00:00 기준) 안에서 실행되는 모든 순간(분). 요일이 없으면 매일."""
    days = time_in.days if time_in.days is not None else legacy_days
    day_indexes = [schedule_config.VALID_DAYS.index(d) for d in days] or range(7)
    return [day * 24 * 60 + time_in.hour * 60 + time_in.minute for day in day_indexes]


def _time_label(time_in: "CronTimeIn", legacy_days: list[str]) -> str:
    days = time_in.days if time_in.days is not None else legacy_days
    day_text = "·".join(_DAY_LABELS[d] for d in schedule_config.VALID_DAYS if d in days)
    return f"{time_in.hour:02d}:{time_in.minute:02d}" + (f", {day_text}" if day_text else "")


def _check_archive_gap_against(archive_in: "JobScheduleIn", download_in: "JobScheduleIn") -> None:
    """요일이 시각마다 달라서 한 주 안의 실제 순간끼리 비교한다 — 같은 날뿐 아니라 자정을 넘겨 다음 날 새벽(일요일에서 월요일 포함)에 겹치는 경우도 잡는다."""
    week = 7 * 24 * 60
    for download_time in download_in.cron_times:
        download_moments = _week_minutes(download_time, download_in.cron_days)
        for archive_time in archive_in.cron_times:
            archive_moments = _week_minutes(archive_time, archive_in.cron_days)
            if any((a - d) % week < 10 for d in download_moments for a in archive_moments):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"아카이빙 시각({_time_label(archive_time, archive_in.cron_days)})은 "
                        f"다운로드 시각({_time_label(download_time, download_in.cron_days)})보다 "
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
            await asyncio.to_thread(schedule_config.set_download_schedules, [schedule_config.from_dict(entry) for entry in job_in])
        else:
            await asyncio.to_thread(schedule_config.set_schedule, job_id, schedule_config.from_dict(job_in))

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await asyncio.to_thread(scheduler_mod.reschedule_all, scheduler)

    return await get_schedules()
