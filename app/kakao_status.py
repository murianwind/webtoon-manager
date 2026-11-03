"""카카오 제외됨/구독해제 작품의 연재 상태(완결/휴재/서비스 종료) 확인 결과.

카카오 요일 목록은 연재 중인 작품만 담고, 완결 여부(is_finished)는 "다운로드를 끝냈을 때"만 저장돼서, 받지 않는 제외됨/구독해제 작품은 완결이나 서비스가 끝난 작품도
그대로 보였다. 그래서 구독 중이 아닌 카카오 작품은 작품 기본 정보(on_issue: Y 연재, P 휴재, N 완결)로 상태를 알아내 여기에 저장한다(tracker.refresh_inactive_kakao_status).
작품 정보가 없다고(HTTP 404/410) 하면 서비스가 끝난 것으로 본다. 일시 오류(네트워크/5xx)는 상태를 바꾸지 않고 하루 뒤에 다시 묻는다.
저장 형태: {작품 번호(문자열): {on_issue, http, at}} — "기타"에 왜 남았는지 진단하는 데도 쓴다(weekday_diagnostics).
"""
import time

from app.json_setting import JsonSetting

LOOKUP_KEY = "kakao_status_lookup"
LOOKUP_BATCH = 40  # 한 번에 알아볼 최대 작품 수
LOOKUP_PAUSE_SECONDS = 0.3  # 작품 사이에 쉬는 시간(카카오에 한꺼번에 몰리지 않게)
HIDDEN_RETRY_DAYS = 30  # 완결/서비스 종료는 이만큼 지나면 다시 확인한다(다시 연재하는 경우만 드물게 있다)
SERVING_RETRY_DAYS = 7  # 연재/휴재는 이만큼 지나면 다시(완결로 바뀔 수 있다)
ERROR_RETRY_DAYS = 1  # 일시 오류(상태를 못 알아냄)는 하루 뒤에 다시
ENDED_HTTP = (404, 410)  # 작품 정보가 없다 = 서비스가 끝났다

_LOOKUP = JsonSetting(LOOKUP_KEY)


def save(series_id: int, *, on_issue: str | None, http: int | None, now: float | None = None) -> None:
    entry = {"on_issue": on_issue, "http": http, "at": now if now is not None else time.time()}
    _LOOKUP.edit(lambda lookup: lookup.update({str(series_id): entry}))


def get_lookup() -> dict[str, dict]:
    return _LOOKUP.load()


def is_ended(entry: dict) -> bool:
    return entry.get("http") in ENDED_HTTP


def is_hidden(entry: dict) -> bool:
    """제외됨/구독해제 화면에서 가릴 상태 — 완결(on_issue N)이거나 서비스가 끝난 작품."""
    return entry.get("on_issue") == "N" or is_ended(entry)


def hidden_ids() -> set[int]:
    return {int(series_id) for series_id, entry in get_lookup().items() if is_hidden(entry)}


def pending_ids(series_ids: list[int], now: float | None = None) -> list[int]:
    """series_ids 중 지금 (다시) 알아봐야 할 작품 — 입력 순서 유지. 완결/종료는 30일, 연재/휴재는 7일, 일시 오류는 1일 동안 다시 묻지 않는다."""
    now = now if now is not None else time.time()
    lookup = get_lookup()
    pending = []
    for series_id in series_ids:
        entry = lookup.get(str(series_id))
        if entry is None:
            pending.append(series_id)
            continue
        keep_days = HIDDEN_RETRY_DAYS if is_hidden(entry) else SERVING_RETRY_DAYS if entry.get("on_issue") in ("Y", "P") else ERROR_RETRY_DAYS
        if now - entry.get("at", 0) >= keep_days * 86400:
            pending.append(series_id)
    return pending
