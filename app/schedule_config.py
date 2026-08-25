"""
잡(discovery/download/commands)별 실행 스케줄 설정.

세 가지 모드를 지원한다:
  - off      : 이 잡을 아예 실행하지 않음
  - interval : N분마다 (기존 방식)
  - cron     : 특정 시:분에, 매일 또는 지정한 요일에만 실행

settings 테이블에 잡마다 JSON 한 덩어리로 저장한다 (schedule_<job_id> 키).
"""

import json
import logging
from dataclasses import asdict, dataclass, field

from app import repository

log = logging.getLogger(__name__)

VALID_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
VALID_MODES = ("off", "interval", "cron")


@dataclass
class JobSchedule:
    mode: str = "interval"  # off | interval | cron
    interval_minutes: int = 60
    cron_hour: int = 3
    cron_minute: int = 0
    cron_days: list[str] = field(default_factory=list)  # 비어있으면 매일

    def sanitized(self) -> "JobSchedule":
        mode = self.mode if self.mode in VALID_MODES else "interval"
        return JobSchedule(
            mode=mode,
            interval_minutes=max(1, int(self.interval_minutes)),
            cron_hour=min(23, max(0, int(self.cron_hour))),
            cron_minute=min(59, max(0, int(self.cron_minute))),
            cron_days=[d for d in self.cron_days if d in VALID_DAYS],
        )


def _key(job_id: str) -> str:
    return f"schedule_{job_id}"


def get_schedule(job_id: str, default: JobSchedule) -> JobSchedule:
    raw = repository.get_setting(_key(job_id))
    if not raw:
        return default
    try:
        data = json.loads(raw)
        return JobSchedule(**data).sanitized()
    except Exception as e:
        log.error("스케줄 설정 파싱 실패 (job=%s): %s — 기본값 사용", job_id, e)
        return default


def set_schedule(job_id: str, schedule: JobSchedule) -> None:
    repository.set_setting(_key(job_id), json.dumps(asdict(schedule.sanitized())))
