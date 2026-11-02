"""
잡(discovery/download/commands)별 실행 스케줄 설정.

다운로드 잡은 스케줄을 여러 개 등록할 수 있고, 스케줄마다 받을 대상(네이버 / 카카오페이지 / 둘 다)을
고른다(예: 네이버는 매시간, 카카오페이지는 하루 세 번). 나머지 잡은 스케줄 하나만 갖는다.

세 가지 모드를 지원한다:
  - off      : 이 잡을 아예 실행하지 않음
  - interval : N분마다 (기존 방식)
  - cron     : 특정 시:분에 실행 — 시각마다 요일을 따로 고른다(아무 요일도 안 고르면 매일)

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


def _clean_days(days) -> list[str]:
    """요일 목록을 월~일 순서로, 잘못된 값과 중복은 빼고 정리한다(빈 목록 = 매일)."""
    chosen = set(days or [])
    return [d for d in VALID_DAYS if d in chosen]


@dataclass
class JobSchedule:
    mode: str = "interval"  # off | interval | cron
    interval_minutes: int = 60
    cron_times: list[dict] = field(default_factory=lambda: [{"hour": 3, "minute": 0, "days": []}])  # 항목마다 {hour, minute, days} — days가 비어 있으면 매일
    target: str = "naver"  # 다운로드 잡: 받을 대상 naver | kakao | both (예전에 저장된 스케줄은 네이버로 취급)

    def sanitized(self) -> "JobSchedule":
        mode = self.mode if self.mode in VALID_MODES else "interval"
        times = []
        for t in self.cron_times or []:
            try:
                times.append({"hour": min(23, max(0, int(t["hour"]))), "minute": min(59, max(0, int(t["minute"]))), "days": _clean_days(t.get("days"))})
            except (KeyError, TypeError, ValueError):
                continue
        if not times:
            times = [{"hour": 3, "minute": 0, "days": []}]
        return JobSchedule(
            mode=mode,
            interval_minutes=max(1, int(self.interval_minutes)),
            cron_times=times,
            target=self.target if self.target in VALID_TARGETS else "naver",
        )


def _key(job_id: str) -> str:
    return f"schedule_{job_id}"


def from_dict(data: dict) -> JobSchedule:
    """저장된 설정이나 화면이 보낸 값에서 스케줄을 만든다. 예전 형식을 그대로 살려서 읽는다:
    - cron_hour/cron_minute 단일 값(여러 시각 cron_times 도입 전)
    - 스케줄 전체에 공통이던 요일 cron_days: 요일이 따로 없는 시각마다 복사한다(동작이 그대로 유지된다)."""
    data = dict(data)
    if "cron_times" not in data and "cron_hour" in data:
        data["cron_times"] = [{"hour": data.pop("cron_hour"), "minute": data.pop("cron_minute", 0)}]
    legacy_days = data.pop("cron_days", None) or []
    if legacy_days:
        data["cron_times"] = [t if t.get("days") is not None else {**t, "days": legacy_days} for t in data.get("cron_times") or []]
    return JobSchedule(**data).sanitized()


def get_schedule(job_id: str, default: JobSchedule) -> JobSchedule:
    raw = repository.get_setting(_key(job_id))
    if not raw:
        return default
    try:
        return from_dict(json.loads(raw))
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
        return [from_dict(item) for item in (data if isinstance(data, list) else [data])]
    except Exception as e:
        log.error("다운로드 스케줄 설정 파싱 실패: %s — 기본값 사용", e)
        return [default]


def set_download_schedules(schedules: list[JobSchedule]) -> None:
    repository.set_setting(_key(_DOWNLOAD_JOB_ID), json.dumps([asdict(s.sanitized()) for s in schedules]))
