"""스케줄러 공통 — 작업별 기본 스케줄(여러 작업 모듈이 같이 쓰므로 맨 아래 계층에 둔다).
"""

from app.schedule_config import JobSchedule

DEFAULT_SCHEDULES: dict[str, JobSchedule] = {
    "discovery_job": JobSchedule(mode="interval", interval_minutes=360),
    "download_job": JobSchedule(mode="interval", interval_minutes=60),
    "report_job": JobSchedule(mode="off"),  # 사용자가 원하는 시각으로 직접 설정해야 켜짐
    "archive_job": JobSchedule(mode="off"),
}
