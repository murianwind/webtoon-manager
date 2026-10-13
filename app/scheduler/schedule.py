"""스케줄 구성 — 작업별 트리거를 만들고 APScheduler에 올린다. 다시 올리기(reschedule_all)도 여기서 한다.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import kakao_catalog, schedule_config
from app.schedule_config import JobSchedule

from app.scheduler.archive_job import (
    run_archive_job,
)
from app.scheduler.common import (
    DEFAULT_SCHEDULES,
)
from app.scheduler.discovery_job import (
    run_discovery_job,
    run_kakao_catalog_refresh_job,
)
from app.scheduler.download_job import (
    run_download_job,
)
from app.scheduler.report_job import (
    run_report_job,
)

_JOB_FUNCS = {
    "discovery_job": run_discovery_job,
    "report_job": run_report_job,
    "archive_job": run_archive_job,
}


def _build_trigger(schedule: JobSchedule):
    """off면 None(=스케줄 없음), interval이면 N분마다, cron이면 지정한 시:분들/요일에.
    hour/minute은 한국시간 기준으로 해석된다(스케줄러 자체가 Asia/Seoul로 고정됨).
    시각을 여러 개 지정할 수 있어서(예: 07:00과 19:30 둘 다), 시각마다 CronTrigger를
    하나씩 만들고 OrTrigger로 묶는다 — hour/minute을 각각 콤마로 나열하면(예:
    hour='7,19', minute='0,30') 두 필드가 독립적으로 평가되어 07:30/19:00처럼
    의도하지 않은 조합까지 걸리므로, 반드시 각 시각을 별개 트리거로 만들어야 한다."""
    if schedule.mode == "off":
        return None
    if schedule.mode == "cron":
        day_of_week = ",".join(schedule.cron_days) if schedule.cron_days else "*"
        triggers = [
            CronTrigger(hour=t["hour"], minute=t["minute"], day_of_week=day_of_week, timezone="Asia/Seoul")
            for t in schedule.cron_times
        ]
        if len(triggers) == 1:
            return triggers[0]
        return OrTrigger(triggers)
    return IntervalTrigger(minutes=schedule.interval_minutes)


def _apply_download_schedules(scheduler: AsyncIOScheduler) -> None:
    """다운로드 잡은 스케줄이 여러 개(각각 대상이 다름)라서, 등록된 것을 전부 지우고 저장된 목록대로 다시 등록한다."""
    for job in scheduler.get_jobs():
        if job.id == "download_job" or job.id.startswith("download_job:"):
            scheduler.remove_job(job.id)
    for index, entry in enumerate(schedule_config.get_download_schedules(DEFAULT_SCHEDULES["download_job"])):
        trigger = _build_trigger(entry)
        if trigger is not None:
            scheduler.add_job(run_download_job, trigger=trigger, id=f"download_job:{index}", kwargs={"target": entry.target})


def _apply_job_schedule(scheduler: AsyncIOScheduler, job_id: str) -> None:
    if job_id == "download_job":
        _apply_download_schedules(scheduler)
        return
    schedule = schedule_config.get_schedule(job_id, DEFAULT_SCHEDULES[job_id])
    trigger = _build_trigger(schedule)
    existing = scheduler.get_job(job_id)

    if trigger is None:
        if existing:
            scheduler.remove_job(job_id)
        return

    if existing:
        scheduler.reschedule_job(job_id, trigger=trigger)
    else:
        scheduler.add_job(_JOB_FUNCS[job_id], trigger=trigger, id=job_id)


MISFIRE_GRACE_SECONDS = 600  # 스케줄 시각보다 이만큼(초) 안으로 늦은 실행은 그대로 실행한다


def create_scheduler() -> AsyncIOScheduler:
    # 컨테이너의 시스템 시간대(보통 UTC)와 무관하게 항상 한국시간으로 해석하도록
    # 명시한다 — 명시 안 하면 APScheduler가 컨테이너의 시스템 기본값(UTC)을 쓰는데,
    # 설정 화면에서 "07:00"이라고 넣으면 사용자는 한국시간 07:00을 기대하지만
    # 실제로는 UTC 07:00(한국시간 오후 4시)에 실행되고 있었다 — 실제로 확인된 버그.
    # 지연 허용 시간: 기본값(1초)이면 앱이 다른 일로 1초만 바빠도 그 회차를 실행하지 않고 버린다 — 실제로 06:50 다운로드가
    # "was missed by 0:00:01.17"로 건너뛰어졌다. 10분 안으로 늦은 건 그대로 실행하고(앱이 잠깐 내려갔다 올라온 경우 포함),
    # 여러 번 밀린 실행은 한 번으로 합친다(coalesce).
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul", job_defaults={"misfire_grace_time": MISFIRE_GRACE_SECONDS, "coalesce": True})
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
    # 사용자가 설정하는 스케줄이 아니라 고정된 내부 작업 — 설정 화면의 스케줄 저장/재등록과 무관하게 항상 돈다
    scheduler.add_job(
        run_kakao_catalog_refresh_job, trigger=IntervalTrigger(minutes=kakao_catalog.REFRESH_INTERVAL_MINUTES),
        id="kakao_catalog_refresh",
    )
    return scheduler


def reschedule_all(scheduler: AsyncIOScheduler) -> None:
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
