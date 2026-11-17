"""회차가 늘지 않는 카카오 웹툰 알림 — 구독 중이고 연재 중인데 회차 수가 N일 이상 그대로인 작품을 하루 한 번 다운로드 리포트에 얹어 알린다.

- 무엇을: 다운로드 때마다 회차 수(on_sale_count)를 기록해서(track), 같은 수가 N일(설정, 기본 30, 0이면 끔) 이상이면 대상이다(due). 휴재/완결 작품은 정체로 세지 않는다
  (연재 중이 아니면 기준일을 매번 오늘로 되돌려서, 연재로 돌아온 날부터 센다). 같은 정체는 한 번만 알리고, 회차가 늘면 다시 센다.
- 언제: 하루(한국시간) 한 번 — 발송하지 않는 시간이 있으면 그날 가장 빠른 종료 시각 이후 첫 다운로드 리포트에, 없으면 그날 첫 리포트에 얹는다.
  리포트가 안 나가는 날은 이 알림만 따로 보낸다(standalone_due) — 시각은 웹툰 전체목록 알림과 같은 규칙(list_alert.plan: 정해 둔 발송 시각, 없으면 발송하지 않는 시간이 끝난 뒤 30분,
  둘 다 없으면 오전 7시)이고, 발송하지 않는 시간에는 미룬다. 보낼 작품이 없는 날도 "확인한 것"으로 친다.
메시지 모양은 scheduler/report_message.py, 리포트에 얹거나 따로 보내는 일은 scheduler/report_job.py.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import discord_config, list_alert, report_dispatch, repository

SETTING_DAYS = "stalled_episodes_days"  # 며칠 이상 회차가 안 늘면 알릴지(0이면 끔, 설정이 없으면 DEFAULT_DAYS)
SETTING_DONE = "stalled_episodes_done_date"  # 마지막으로 알림을 처리한 날(한국시간 날짜, YYYY-MM-DD)
SETTING_RETRY = "stalled_episodes_retry_at"  # 단독 전송이 실패했을 때 다시 시도할 수 있게 되는 시각(UTC ISO)
DEFAULT_DAYS = 30
MAX_DAYS = 365
RETRY_MINUTES = 10  # 단독 전송이 실패하면 이만큼 뒤에 다시 시도한다

_KST = ZoneInfo("Asia/Seoul")


def get_days() -> int:
    value = repository.get_setting(SETTING_DAYS)
    return DEFAULT_DAYS if value is None else int(value)


def set_days(days: int) -> None:
    """0~365일. 꺼져 있다가(0) 켜면 이전 기록을 지워서 처음부터 센다(꺼져 있던 동안의 오래된 기록이 바로 알림이 되지 않게). 범위 밖이면 ValueError."""
    if not 0 <= days <= MAX_DAYS:
        raise ValueError(f"일수는 0~{MAX_DAYS} 사이여야 합니다(0이면 끔).")
    was_off = get_days() == 0
    repository.set_setting(SETTING_DAYS, str(days))
    if was_off and days > 0:
        repository.reset_kakao_episode_counts()


def _today(now: datetime) -> date:
    return now.astimezone(_KST).date()


def track(title_id: int, count: int | None, serving: bool, now: datetime) -> None:
    """구독 중인 카카오 작품의 이번 확인 결과를 기록한다 — count: 작품의 회차 수(모르면 None), serving: 연재 중인가. 꺼져 있으면 아무것도 안 한다.
    처음 보거나 회차가 달라졌거나 연재 중이 아니면 오늘을 기준일로 새로 센다(알림 기록도 지운다)."""
    if get_days() == 0 or count is None:
        return
    webtoon = repository.get_kakao_webtoon(title_id)
    if webtoon is None:
        return
    if not serving or webtoon["episode_count_seen"] != count:
        repository.set_kakao_episode_count(title_id, count, _today(now).isoformat())


def due(now: datetime) -> list[dict]:
    """알릴 작품들 — {"title_id", "title", "count", "days"(그대로인 날 수)}. 꺼져 있으면 빈 목록."""
    days = get_days()
    if days == 0:
        return []
    today = _today(now)
    cutoff = (today - timedelta(days=days)).isoformat()
    return [
        {"title_id": w["title_id"], "title": w["title"], "count": w["episode_count_seen"], "days": (today - date.fromisoformat(w["episode_count_since"])).days}
        for w in repository.list_kakao_stalled(cutoff)
    ]


def mark_notified(title_ids: list[int]) -> None:
    repository.mark_kakao_stall_notified(title_ids)


def is_done(now: datetime) -> bool:
    return repository.get_setting(SETTING_DONE) == _today(now).isoformat()


def mark_done(now: datetime) -> None:
    repository.set_setting(SETTING_DONE, _today(now).isoformat())


def should_attach(now: datetime) -> bool:
    """지금 나가는 다운로드 리포트에 이 알림을 얹어도 되는가 — 켜져 있고 오늘 아직 안 했고, 발송하지 않는 시간이 있으면 그날 가장 빠른 종료 시각 이후(끝나는 구간이 없는 날은 언제든)."""
    if get_days() == 0 or is_done(now):
        return False
    if not report_dispatch.get_quiet_hours():
        return True
    ends = report_dispatch.quiet_end_minutes(now.astimezone(_KST).weekday())
    local = now.astimezone(_KST)
    return not ends or local.hour * 60 + local.minute >= min(ends)


def note_failure(now: datetime) -> None:
    """단독 전송이 실패했다 — RETRY_MINUTES 뒤에 다시 시도한다(그날은 아직 한 것이 아니다)."""
    repository.set_setting(SETTING_RETRY, (now + timedelta(minutes=RETRY_MINUTES)).isoformat())


def _retry_blocked(now: datetime) -> bool:
    value = repository.get_setting(SETTING_RETRY)
    return bool(value) and now < datetime.fromisoformat(value)


def standalone_due(now: datetime) -> bool:
    """리포트에 못 얹혀서 이 알림만 따로 보낼 때가 됐는가 — 켜져 있고 오늘 아직 안 했고, 디스코드가 있고 발송하지 않는 시간이 아니고 재시도 대기 중이 아니며,
    웹툰 전체목록 알림과 같은 기준 시각(list_alert.plan)이 지났을 때."""
    if get_days() == 0 or is_done(now) or not discord_config.get_webhook_url() or report_dispatch.is_quiet(now) or _retry_blocked(now):
        return False
    current = list_alert.plan(now)
    if current.anchor_minute is None:
        return False
    local = now.astimezone(_KST)
    wait = list_alert.FALLBACK_MINUTES if current.mode == "quiet" else 0
    return local.hour * 60 + local.minute >= current.anchor_minute + wait
