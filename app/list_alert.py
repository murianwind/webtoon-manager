"""웹툰 전체목록 알림(신규 웹툰 + 미등록 작품의 새 에피소드)이 **언제 나가는지**의 규칙 — 하루(한국시간) 한 번만 나간다. 무엇을 담는지는 scheduler/report_sections.py.

- 새 에피소드 발송 시각(`list_alert_time`)을 사용자가 정해 뒀으면 그 시각에 이 알림만 단독으로 나간다(다운로드 리포트에는 안 얹는다).
- 시각을 안 정했고 발송하지 않는 시간이 있으면 → 그날 끝나는 발송하지 않는 시간 중 가장 빠른 종료 시각 이후 처음 나가는 다운로드 리포트에 얹는다.
  그 시각 뒤 30분 안에 다운로드 리포트가 안 나가면 이 알림만 묶어서 따로 보낸다. 그날 끝나는 구간이 없는 요일이면 그날 첫 리포트에 얹고 단독 발송은 없다.
- 둘 다 없으면(예전부터 쓰던 사용자) 기본 시각(오전 7시)에 단독으로 나간다.
- 디스코드가 설정돼 있지 않거나 "미등록 웹툰 새 에피소드 섹션" 옵션이 꺼져 있으면 나가지 않고, 발송하지 않는 시간에는 단독 발송도 미룬다(끝나면 그날 안에 나간다).
"하루 한 번"은 나갔을 때(얹어서든 단독이든)뿐 아니라 그날 확인했는데 보낼 것이 없었을 때도 채운다 — 그날 늦게 생긴 새 에피소드는 다음 날 알린다.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import discord_config, report_dispatch, repository

SETTING_TIME = "list_alert_time"  # 사용자가 정한 발송 시각 "HH:MM"(없으면 정하지 않음)
SETTING_DONE = "list_alert_done_date"  # 마지막으로 알림을 처리한 날(한국시간 날짜, YYYY-MM-DD)
SETTING_RETRY = "list_alert_retry_at"  # 전송이 실패했을 때 다시 시도할 수 있게 되는 시각(UTC ISO)
SETTING_ENABLED = "report_unregistered_new_episodes_enabled"  # 섹션 옵션(예전 이름 그대로 — 꺼져 있으면 "0")
DEFAULT_TIME = "07:00"
RETRY_MINUTES = 10  # 단독 발송이 실패하면 이만큼 뒤에 다시 시도한다(1분마다 확인하므로 매 분 반복하지 않게)
FALLBACK_MINUTES = 30  # 발송하지 않는 시간이 끝난 뒤 이만큼 안에 다운로드 리포트가 안 나가면 이 알림만 따로 보낸다

_KST = ZoneInfo("Asia/Seoul")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{1,2})$")


@dataclass(frozen=True)
class Plan:
    mode: str  # "time": 정해진 시각에 단독 발송 / "quiet": 발송하지 않는 시간이 끝난 뒤 다운로드 리포트에 얹기(+30분 뒤 단독)
    anchor_minute: int | None  # 그날의 기준 시각(분, 0~1439). quiet인데 그날 끝나는 구간이 없으면 None(그날 첫 리포트에 얹기만)


def _to_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def get_time() -> str | None:
    """사용자가 정한 발송 시각(없으면 None — 기본 시각은 DEFAULT_TIME)."""
    value = repository.get_setting(SETTING_TIME)
    return value or None


def set_time(value: str | None) -> None:
    """발송 시각을 저장한다("HH:MM", 한 자리 시/분도 받는다). None이면 지운다. 잘못된 값은 ValueError."""
    if value is None:
        repository.set_setting(SETTING_TIME, None)
        return
    match = _TIME_RE.match(value.strip())
    if not match or not (0 <= int(match.group(1)) <= 23 and 0 <= int(match.group(2)) <= 59):
        raise ValueError("발송 시각은 00:00~23:59 형식이어야 합니다.")
    repository.set_setting(SETTING_TIME, f"{int(match.group(1)):02d}:{int(match.group(2)):02d}")


def plan(now: datetime) -> Plan:
    """지금 날짜에 적용할 방식과 기준 시각. 시각을 정해 뒀으면 그것이 먼저다(발송하지 않는 시간을 나중에 설정하고 기존 발송 시각을 유지하기로 한 경우 포함)."""
    explicit = get_time()
    if explicit:
        return Plan("time", _to_minutes(explicit))
    if report_dispatch.get_quiet_hours():
        ends = report_dispatch.quiet_end_minutes(now.astimezone(_KST).weekday())
        return Plan("quiet", min(ends) if ends else None)
    return Plan("time", _to_minutes(DEFAULT_TIME))


def _today(now: datetime) -> str:
    return now.astimezone(_KST).date().isoformat()


def is_done(now: datetime) -> bool:
    return repository.get_setting(SETTING_DONE) == _today(now)


def mark_done(now: datetime) -> None:
    repository.set_setting(SETTING_DONE, _today(now))


def note_failure(now: datetime) -> None:
    """단독 발송이 실패했다 — RETRY_MINUTES 뒤에 다시 시도한다(그날은 아직 한 것이 아니다)."""
    repository.set_setting(SETTING_RETRY, (now + timedelta(minutes=RETRY_MINUTES)).isoformat())


def _retry_blocked(now: datetime) -> bool:
    value = repository.get_setting(SETTING_RETRY)
    return bool(value) and now < datetime.fromisoformat(value)


def _minute_of_day(now: datetime) -> int:
    local = now.astimezone(_KST)
    return local.hour * 60 + local.minute


def _enabled() -> bool:
    return repository.get_setting(SETTING_ENABLED) != "0"


def should_attach(now: datetime) -> bool:
    """다운로드 리포트에 이 알림을 얹어도 되는가 — 발송하지 않는 시간 방식에서, 그날 기준 시각(가장 빠른 종료 시각)이 지난 뒤(끝나는 구간이 없는 날은 언제든)."""
    if not _enabled() or is_done(now):
        return False
    current = plan(now)
    if current.mode != "quiet":
        return False
    return current.anchor_minute is None or _minute_of_day(now) >= current.anchor_minute


def standalone_due(now: datetime) -> bool:
    """이 알림만 따로 보낼 때가 됐는가 — 시각 방식은 그 시각부터, 발송하지 않는 시간 방식은 기준 시각 + 30분부터(그 사이 다운로드 리포트가 안 나갔을 때). 디스코드가 없거나 발송하지 않는 시간이면 아직."""
    if not _enabled() or is_done(now) or not discord_config.get_webhook_url() or report_dispatch.is_quiet(now) or _retry_blocked(now):
        return False
    current = plan(now)
    if current.anchor_minute is None:
        return False
    wait = FALLBACK_MINUTES if current.mode == "quiet" else 0
    return _minute_of_day(now) >= current.anchor_minute + wait
