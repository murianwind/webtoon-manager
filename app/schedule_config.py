"""
잡(discovery/download/commands)별 실행 스케줄 설정.

다운로드 잡은 스케줄을 여러 개 등록할 수 있고, 스케줄마다 받을 대상(네이버 / 카카오페이지 / 둘 다)을
고른다(예: 네이버는 매시간, 카카오페이지는 하루 세 번). 나머지 잡은 스케줄 하나만 갖는다.

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
VALID_TARGETS = ("naver", "kakao", "both")  # 다운로드 잡에서만 의미가 있다


@dataclass
class JobSchedule:
    mode: str = "interval"  # off | interval | cron
    interval_minutes: int = 60
    cron_times: list[dict] = field(default_factory=lambda: [{"hour": 3, "minute": 0}])
    cron_days: list[str] = field(default_factory=list)  # 비어있으면 매일
    target: str = "naver"  # 다운로드 잡: 받을 대상 naver | kakao | both (예전에 저장된 스케줄은 네이버로 취급)

    def sanitized(self) -> "JobSchedule":
        mode = self.mode if self.mode in VALID_MODES else "interval"
        times = []
        for t in self.cron_times or []:
            try:
                times.append({"hour": min(23, max(0, int(t["hour"]))), "minute": min(59, max(0, int(t["minute"])))})
            except (KeyError, TypeError, ValueError):
                continue
        if not times:
            times = [{"hour": 3, "minute": 0}]
        return JobSchedule(
            mode=mode,
            interval_minutes=max(1, int(self.interval_minutes)),
            cron_times=times,
            cron_days=[d for d in self.cron_days if d in VALID_DAYS],
            target=self.target if self.target in VALID_TARGETS else "naver",
        )


def _key(job_id: str) -> str:
    return f"schedule_{job_id}"


def _from_dict(data: dict) -> JobSchedule:
    # 예전 버전(cron_hour/cron_minute 단일값)으로 저장된 데이터 호환 처리 —
    # 여러 시각(cron_times) 도입 전에 저장된 설정을 그대로 살려서 쓴다.
    data = dict(data)
    if "cron_times" not in data and "cron_hour" in data:
        data["cron_times"] = [{"hour": data.pop("cron_hour"), "minute": data.pop("cron_minute", 0)}]
    return JobSchedule(**data).sanitized()


def get_schedule(job_id: str, default: JobSchedule) -> JobSchedule:
    raw = repository.get_setting(_key(job_id))
    if not raw:
        return default
    try:
        return _from_dict(json.loads(raw))
    except Exception as e:
        log.error("스케줄 설정 파싱 실패 (job=%s): %s — 기본값 사용", job_id, e)
        return default


def set_schedule(job_id: str, schedule: JobSchedule) -> None:
    repository.set_setting(_key(job_id), json.dumps(asdict(schedule.sanitized())))


_DOWNLOAD_JOB_ID = "download_job"


def get_download_schedules(default: JobSchedule) -> list[JobSchedule]:
    """다운로드 잡의 스케줄 목록. 저장된 게 없으면 기본 스케줄 하나. 예전에 하나만 저장돼 있던 설정은 그 하나(대상은
    네이버)로 읽는다. 빈 목록도 유효하다(다운로드를 전부 끈 상태)."""
    raw = repository.get_setting(_key(_DOWNLOAD_JOB_ID))
    if not raw:
        return [default]
    try:
        data = json.loads(raw)
        return [_from_dict(item) for item in (data if isinstance(data, list) else [data])]
    except Exception as e:
        log.error("다운로드 스케줄 설정 파싱 실패: %s — 기본값 사용", e)
        return [default]


def set_download_schedules(schedules: list[JobSchedule]) -> None:
    repository.set_setting(_key(_DOWNLOAD_JOB_ID), json.dumps([asdict(s.sanitized()) for s in schedules]))
