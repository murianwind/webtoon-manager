"""작업 실행/진행상황/이력: 지금 실행, 실행 상태, 실행 이력, 메타 동기화."""


import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import (
    job_status,
    repository,
    schedule_config,
    tracker,
)
from app import scheduler as scheduler_mod
from app.config import get_settings

from app.api.common import (
    RetentionDaysIn,
    RetentionDaysOut,
)


log = logging.getLogger(__name__)

router = APIRouter()



@router.post("/metadata/sync")
async def sync_metadata():
    """추적 중인 웹툰 폴더를 스캔해서 info.xml/cover.jpg 누락분만 다시 만든다."""
    async def _run():
        settings = get_settings()
        job_status.start("metadata_sync")
        job_status.log_line("metadata_sync", "메타 동기화 시작")
        try:
            count = await tracker.sync_metadata_for_all(settings)
            job_status.log_line("metadata_sync", f"{count}개 웹툰 정리 완료")
            job_status.finish("metadata_sync", success=True)
        except Exception as e:
            job_status.log_line("metadata_sync", f"오류: {e}")
            job_status.finish("metadata_sync", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


@router.post("/jobs/report/run")
async def run_report_job_now():
    """수동 버튼으로 누른 실행은 실제 테스트 목적이므로, 지난 발송 이후 기록이
    없어도 최근 기록으로라도 발송해서 실제로 잘 오는지 확인할 수 있게 한다."""
    asyncio.create_task(scheduler_mod.run_report_job(force_test=True))
    return {"status": "started"}


@router.get("/jobs/status")
async def jobs_status():
    return await asyncio.to_thread(job_status.snapshot)


@router.get("/jobs/history")
async def jobs_history(limit_per_job: int = 10):
    """스케줄대로 자동 실행된 잡이 실제로 돌았는지/성공했는지 나중에 확인할 수 있는 이력."""
    return await asyncio.to_thread(repository.list_job_history, limit_per_job)


@router.delete("/jobs/history/{entry_id}")
async def delete_job_history_entry(entry_id: int):
    await asyncio.to_thread(repository.delete_job_history_entry, entry_id)
    return {"status": "deleted"}


@router.delete("/jobs/history")
async def clear_all_job_history():
    await asyncio.to_thread(repository.clear_job_history)
    return {"status": "cleared"}


_KEY_JOB_HISTORY_RETENTION_DAYS = "job_history_retention_days"


@router.get("/jobs/history/retention-days", response_model=RetentionDaysOut)
async def get_job_history_retention_days():
    value = await asyncio.to_thread(repository.get_setting, _KEY_JOB_HISTORY_RETENTION_DAYS)
    return RetentionDaysOut(retention_days=int(value) if value else 0)


@router.post("/jobs/history/retention-days", response_model=RetentionDaysOut)
async def set_job_history_retention_days(payload: RetentionDaysIn):
    await asyncio.to_thread(
        repository.set_setting, _KEY_JOB_HISTORY_RETENTION_DAYS, str(payload.retention_days) if payload.retention_days > 0 else None
    )
    return await get_job_history_retention_days()


@router.post("/jobs/discovery/run")
async def trigger_discovery_job():
    asyncio.create_task(scheduler_mod.run_discovery_job())
    return {"status": "started"}


def _default_manual_download_target() -> str:
    """"지금 실행"을 대상 없이 부르면, 등록된(꺼지지 않은) 다운로드 스케줄들이 받는 플랫폼 전체를 받는다. 스케줄이
    없으면 네이버만(예전 동작)."""
    platforms: set[str] = set()
    for entry in schedule_config.get_download_schedules(scheduler_mod.DEFAULT_SCHEDULES["download_job"]):
        if entry.mode != "off":
            platforms |= {"naver", "kakao"} if entry.target == "both" else {entry.target}
    if platforms == {"kakao"}:
        return "kakao"
    return "both" if platforms == {"naver", "kakao"} else "naver"


@router.post("/jobs/download/run")
async def trigger_download_job(target: str | None = None):
    if target is None:
        target = await asyncio.to_thread(_default_manual_download_target)
    if target not in schedule_config.VALID_TARGETS:
        raise HTTPException(status_code=400, detail=f"대상은 {schedule_config.VALID_TARGETS} 중 하나여야 합니다.")
    asyncio.create_task(scheduler_mod.run_download_job(target))
    return {"status": "started", "target": target}
