"""다운로드 리포트 발송 흐름의 설정과 상태.

예전에는 "지정한 시각마다" 리포트를 보냈다. 이제는 다운로드가 끝나면 보낸다 — 단 아래 규칙으로:
- 모으기(batch_minutes, 기본 10분): 다운로드가 끝나고 N분 동안 새 다운로드가 없으면 한 묶음으로 보고 한 통을 보낸다(그 안에 또 끝나면 N분을 다시 센다).
- 발송하지 않는 시간(quiet_hours): 이 시간에는 보내지 않고 쌓아 두었다가 끝나면 한 통으로 몰아서 보낸다. 구간마다 요일(시작하는 날 기준, 자정을 넘길 수 있음)을 고르고 겹치면 저장할 때 오류.
- 묶음이 확정되면(발송 전) 뷰어 서버 스캔을 먼저 시킨다(viewer_scan) — 스캔은 발송하지 않는 시간과 상관없이 하고 리포트만 끝날 때까지 미룬다.

상태(state)는 설정 테이블에 JSON 한 덩어리로 저장해서 재시작해도 이어진다. 1분마다 도는 확인(scheduler.report_job.run_report_tick)이 이 상태만 보고 움직인다:
  last_download_at: 마지막으로 다운로드가 끝난 시각(없으면 보낼 것이 대기 중이 아님) / cutoff: 확정된 묶음의 끝(이 시각까지의 이력을 보낸다)
  scan_requested: 이 묶음에서 뷰어 스캔을 이미 요청했다 / scan_started_at: 리포트가 스캔을 기다리기 시작한 시각(최대 대기 계산용) / scan_done: 기다림이 끝났다(스캔 반영/실패/시간 초과).
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import job_status, repository
from app.json_setting import JsonSetting
from app.schedule_config import VALID_DAYS

QUIET_KEY = "report_quiet_hours"
BATCH_KEY = "report_batch_minutes"
STATE_KEY = "report_dispatch_state"
LAST_SENT_KEY = "report_last_sent_at"
DEFAULT_BATCH_MINUTES = 10
MAX_BATCH_MINUTES = 24 * 60
DOWNLOAD_JOBS = ("download", "manual")  # 이 작업들이 끝나면 "다운로드가 끝났다"로 센다

_KST = ZoneInfo("Asia/Seoul")
_DAY_LABELS = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}
_DAY = 24 * 60
_WEEK = 7 * _DAY


# ── 발송하지 않는 시간 ───────────────────────────────────────────────

def _label(r: dict) -> str:
    days = "·".join(_DAY_LABELS[d] for d in r["days"]) or "매일"
    return f"{r['start_hour']:02d}:{r['start_minute']:02d}~{r['end_hour']:02d}:{r['end_minute']:02d}({days})"


def _intervals(r: dict) -> list[tuple[int, int]]:
    """이 구간이 한 주(월 00:00 기준, 분) 안에서 차지하는 [시작, 끝) 목록 — 주를 넘기면 둘로 나눈다. 요일은 시작하는 날 기준."""
    start = r["start_hour"] * 60 + r["start_minute"]
    length = (r["end_hour"] * 60 + r["end_minute"] - start) % _DAY
    out = []
    for day in [VALID_DAYS.index(d) for d in r["days"]] or range(7):
        s = day * _DAY + start
        e = s + length
        out += [(s, e)] if e <= _WEEK else [(s, _WEEK), (0, e - _WEEK)]
    return out


def _parse_quiet_interval(raw: dict) -> dict:
    """구간 하나를 검증하고 정리한다(시각 범위, 요일, 시작=끝 금지). 잘못되면 사람이 읽을 이유와 함께 ValueError."""
    try:
        sh, sm, eh, em = (int(raw[k]) for k in ("start_hour", "start_minute", "end_hour", "end_minute"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("발송하지 않는 시간의 시작/끝 시각이 올바르지 않습니다.") from None
    if not (0 <= sh <= 23 and 0 <= eh <= 23):
        raise ValueError("시(hour)는 0~23이어야 합니다.")
    if not (0 <= sm <= 59 and 0 <= em <= 59):
        raise ValueError("분(minute)은 0~59여야 합니다.")
    days = raw.get("days") or []
    invalid = [d for d in days if d not in VALID_DAYS]
    if invalid:
        raise ValueError(f"알 수 없는 요일: {invalid}")
    if (sh, sm) == (eh, em):
        raise ValueError(f"발송하지 않는 시간의 시작과 끝이 같습니다({sh:02d}:{sm:02d}) — 같은 시각이면 구간이 없습니다.")
    return {"start_hour": sh, "start_minute": sm, "end_hour": eh, "end_minute": em, "days": [d for d in VALID_DAYS if d in set(days)]}


def _reject_overlaps(intervals: list[dict]) -> None:
    """구간끼리 겹치면(요일은 시작하는 날 기준, 자정/주를 넘기는 것까지 따진다) 어느 둘이 겹치는지 밝혀 ValueError."""
    for i, a in enumerate(intervals):
        for b in intervals[i + 1:]:
            if any(s1 < e2 and s2 < e1 for s1, e1 in _intervals(a) for s2, e2 in _intervals(b)):
                raise ValueError(f"발송하지 않는 시간 {_label(a)}와 {_label(b)}이 겹칩니다. 겹치지 않게 고쳐 주세요.")


def parse_quiet_hours(items) -> list[dict]:
    """화면/설정에서 온 구간 목록을 검증하고 정리한다. 잘못된 값이나 겹침이 있으면 사람이 읽을 이유와 함께 ValueError."""
    result = [_parse_quiet_interval(raw) for raw in items or []]
    _reject_overlaps(result)
    return result


def get_quiet_hours() -> list[dict]:
    raw = repository.get_setting(QUIET_KEY)
    try:
        return parse_quiet_hours(json.loads(raw)) if raw else []
    except ValueError:  # 깨진 JSON이나 잘못된 값(수동 편집 등)은 "지정 없음"으로 본다
        return []


def set_quiet_hours(items) -> None:
    repository.set_setting(QUIET_KEY, json.dumps(parse_quiet_hours(items), ensure_ascii=False))


def is_quiet(now: datetime) -> bool:
    """지금이 발송하지 않는 시간인가(한국시간 기준, 끝 시각은 포함하지 않는다)."""
    local = now.astimezone(_KST)
    week_minute = local.weekday() * _DAY + local.hour * 60 + local.minute
    return any(s <= week_minute < e for r in get_quiet_hours() for s, e in _intervals(r))


# ── 모으는 시간(N분) ─────────────────────────────────────────────────

def get_batch_minutes() -> int:
    raw = repository.get_setting(BATCH_KEY)
    try:
        return min(MAX_BATCH_MINUTES, max(0, int(raw))) if raw is not None else DEFAULT_BATCH_MINUTES
    except ValueError:
        return DEFAULT_BATCH_MINUTES


def set_batch_minutes(minutes: int) -> None:
    if not (0 <= minutes <= MAX_BATCH_MINUTES):
        raise ValueError(f"모으는 시간은 0~{MAX_BATCH_MINUTES}분이어야 합니다(0이면 바로 보냅니다).")
    repository.set_setting(BATCH_KEY, str(minutes))


# ── 발송 상태 ─────────────────────────────────────────────────────────

def _empty_state() -> dict:
    return {"last_download_at": None, "cutoff": None, "scan_requested": False, "scan_started_at": None, "scan_done": False}


# 발송 상태 — 설정에 JSON 한 덩어리로 저장한다(읽고-바꾸고-쓰기는 JsonSetting이 잠금으로 한 덩어리로 보호한다: 작업 종료 알림 등 다른 스레드와 겹쳐도 안전).
_STATE = JsonSetting(STATE_KEY, _empty_state)
load_state = _STATE.load
update_state = _STATE.update  # 이 칸들만 덮어쓴다(다운로드가 끝났다는 기록 같은 다른 칸을 건드리지 않게)
clear_state = _STATE.clear


def finish_cycle(cutoff: str) -> None:
    """cutoff까지의 묶음을 보냈거나 보낼 것이 없어서 끝낸다. 그 사이에 새 다운로드가 끝났으면(cutoff보다 뒤) 다음 묶음으로 남겨 두고, 아니면 상태를 비운다."""
    with _STATE.lock:
        latest = load_state()
        if latest["last_download_at"] and latest["last_download_at"] > cutoff:
            _STATE.save({**_empty_state(), "last_download_at": latest["last_download_at"]})
        else:
            clear_state()


def note_download_finished(now: datetime | None = None, started_at: str | None = None) -> None:
    """다운로드가 하나 끝났다고 기록한다(보낼 이력이 있는지는 보낼 때 이력으로 확인한다).
    started_at: 이 다운로드 작업이 시작한 시각 — 발송 기준 시각이 아직 없을 때(처음 한 번) 이 시각부터의 이력을 보내도록 기준으로 삼는다."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if repository.get_setting(LAST_SENT_KEY) is None and started_at:
        repository.set_setting(LAST_SENT_KEY, started_at)
    with _STATE.lock:
        state = load_state()
        fields = {"last_download_at": now.isoformat()}
        # 묶음이 확정됐어도 아직 스캔이 진행 중이 아니면(시작 전이거나, 끝나고 발송하지 않는 시간을 기다리는 중) 다시 열어서 새로 끝난 다운로드까지 한 통으로 보낸다.
        if state["cutoff"] and not (state["scan_started_at"] and not state["scan_done"]):
            fields.update(cutoff=None, scan_requested=False, scan_started_at=None, scan_done=False)
        update_state(**fields)


def on_job_finished(job_name: str) -> None:
    """job_status.finish 리스너 — 다운로드 작업(정기/수동/충전 시각)이 끝날 때마다 불린다."""
    if job_name in DOWNLOAD_JOBS:
        note_download_finished(started_at=job_status.snapshot()[job_name]["started_at"])


def start() -> None:
    """프로그램이 시작될 때 한 번: 작업이 끝나면 알도록 연결하고, 예전 "발송 시각" 스케줄 설정은 지운다(이제는 다운로드가 끝날 때 보낸다)."""
    job_status.add_finish_listener(on_job_finished)
    repository.set_setting("schedule_report_job", None)

