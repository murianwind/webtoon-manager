"""카카오 기다무 충전 시각에 맞춰 받기 — 작품마다 서버가 알려 준 "다음 충전 시각"을 저장하고, 그 시각이 되면 그 작품만 받게 한다.

왜 타이머가 아니라 저장된 시각인가: 기다무는 작품마다 충전 주기가 3시간/1일/3일 등으로 달라서 앱이 직접 세면 어긋난다. 서버가 주는 절대 시각을 그대로
저장해 두면 주기가 달라도 따로 계산할 필요가 없고, 컨테이너를 재시작해도 남는다. 1분마다 도는 확인(waitfree_job)은 서버에 요청하지 않고 이 값만 본다.

시각을 알게 되는 곳: 기다무로 회차를 열 때(ticket/use 응답의 waitfree_charged_at), 아직 충전 전이라는 확인(ticket/my의 charged_at).
받을 필요가 없어지면(기다릴 잠긴 회차가 없음) 지운다.

서버 부담/밴 방지: 충전 시각 직후가 아니라 작품마다 정해진 30~120초 뒤에 받고(한꺼번에 몰리지 않게, 서버 반영 지연도 흡수), 받고도 충전 시각이 그대로면
10분 뒤, 오류/로그인 문제면 30분 뒤로 미룬다.
"""
import zlib
from datetime import datetime, timedelta, timezone

from app import repository

SETTING_KEY = "waitfree_sync_enabled"
RETRY_MINUTES = 10  # 받았는데 충전 시각이 그대로(안 움직임)일 때 다시 확인할 때까지
ERROR_MINUTES = 30  # 오류/로그인 문제일 때 다시 시도할 때까지
_JITTER_MIN_SECONDS, _JITTER_MAX_SECONDS = 30, 120
_KST = timezone(timedelta(hours=9))


def is_enabled() -> bool:
    return repository.get_setting(SETTING_KEY) == "1"


def set_enabled(enabled: bool) -> None:
    repository.set_setting(SETTING_KEY, "1" if enabled else None)


def _parse(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed.replace(tzinfo=_KST).astimezone(timezone.utc)  # 시간대가 없으면 한국시간으로 본다


def normalize(value) -> str | None:
    """서버가 준 시각(예: 2026-10-13T19:01:59+09:00)을 UTC ISO로. 못 읽으면 None."""
    parsed = _parse(value) if value else None
    return parsed.isoformat() if parsed else None


def remember(series_id: int, value) -> None:
    """작품의 다음 충전 시각을 저장한다. 시각을 모르거나 못 읽는 값이면 기존 값을 그대로 둔다."""
    normalized = normalize(value)
    if normalized:
        repository.set_kakao_waitfree_next_at(series_id, normalized)


def forget(series_id: int) -> None:
    """더 기다릴 필요가 없는 작품(기다무로 열 잠긴 회차가 없음)의 충전 시각을 지운다."""
    repository.set_kakao_waitfree_next_at(series_id, None)


def delay(series_id: int, minutes: int, now: datetime) -> None:
    """같은 작품을 연속으로 두드리지 않도록 다음 시도를 minutes분 뒤로 미룬다."""
    repository.set_kakao_waitfree_next_at(series_id, (now + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat())


def jitter_seconds(series_id: int, next_at: str) -> int:
    """작품마다 정해진 30~120초 — 같은 (작품, 충전 시각)이면 틱마다 바뀌지 않는다."""
    return _JITTER_MIN_SECONDS + zlib.crc32(f"{series_id}|{next_at}".encode()) % (_JITTER_MAX_SECONDS - _JITTER_MIN_SECONDS + 1)


def due_at(series_id: int, next_at: str) -> datetime:
    """충전 시각 + 지터 = 이 작품을 받기 시작하는 시각."""
    return _parse(next_at) + timedelta(seconds=jitter_seconds(series_id, next_at))


def is_waiting(webtoon: dict, now: datetime) -> bool:
    """충전 시각을 알고 아직 받을 때가 아니면 True — 정기 다운로드가 건너뛴다."""
    next_at = webtoon.get("waitfree_next_at")
    return bool(next_at) and _parse(next_at) is not None and due_at(webtoon["title_id"], next_at) > now


def due_webtoons(now: datetime) -> list[dict]:
    """받을 때가 된 작품(충전 시각 + 지터가 지남)을 예정 시각 순으로."""
    due = [w for w in repository.list_kakao_waitfree_schedule() if not is_waiting(w, now)]
    return sorted(due, key=lambda w: due_at(w["title_id"], w["waitfree_next_at"]))


def schedule_view() -> list[dict]:
    """화면용 — 구독 중이고 충전 시각을 아는 작품을 다운로드 예정 시각 순으로."""
    items = [
        {"title_id": w["title_id"], "title": w["title"], "next_at": w["waitfree_next_at"], "due_at": due_at(w["title_id"], w["waitfree_next_at"]).isoformat()}
        for w in repository.list_kakao_waitfree_schedule()
        if _parse(w["waitfree_next_at"]) is not None
    ]
    return sorted(items, key=lambda i: i["due_at"])
