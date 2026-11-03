"""웹툰 목록의 요일 표기 통일과 네이버 요일 지도 저장.

화면(전체목록/제외됨의 요일별 탭)은 모든 목록 응답에서 같은 형식의 요일(mon~sun, 월~일 순서)을 기대한다:
- 네이버: 요일별 목록 API의 MONDAY~SUNDAY를 바꾼다(모르는 값은 무시).
- 카카오: 요일 탭(1~7 = 월~일)마다 나온 작품에 그 요일을 붙인다(kakao_api).
제외됨/구독해제 탭은 네이버를 부르지 않고 DB 기록으로 목록을 만들어서 요일을 모른다 — 전체목록을 불러올 때(네이버 목록을 어차피 받는다) 요일을 설정 테이블에
{작품 번호: [요일]}로 저장해 두고 그 탭들이 거기서 채운다. 요일별 목록에 없는 작품(완결/장기 휴재)은 요일이 없다 — 화면에서 "기타"에 모인다. 그중 완결이 아닌 추적 작품은 작품 페이지에서 요일을 알아내 채운다(아래 LOOKUP_*).
"""
import json
import re
import time

from app import repository

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NAVER_MAP_KEY = "naver_weekday_map"
_NAVER_DAYS = {"MONDAY": "mon", "TUESDAY": "tue", "WEDNESDAY": "wed", "THURSDAY": "thu", "FRIDAY": "fri", "SATURDAY": "sat", "SUNDAY": "sun"}


def from_naver(codes) -> list[str]:
    """네이버의 MONDAY~SUNDAY를 mon~sun으로(월~일 순서, 중복/모르는 값 제외)."""
    found = {_NAVER_DAYS[code] for code in codes or [] if code in _NAVER_DAYS}
    return [day for day in DAYS if day in found]


def _live_map() -> dict[str, list[str]]:
    raw = repository.get_setting(NAVER_MAP_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def get_naver_map() -> dict[str, list[str]]:
    """{작품 번호: [요일]} — 전체목록(요일별 목록)에서 받은 요일이 우선이고, 없으면 작품 페이지에서 알아낸 요일(조회 결과)을 쓴다."""
    lookup = {title_id: entry["days"] for title_id, entry in _get_lookup().items() if entry.get("ok") and entry.get("days")}
    return {**lookup, **_live_map()}


def save_naver_map(days_by_id: dict[str, list[str]]) -> None:
    """네이버 요일별 목록에서 받은 {작품 번호: [요일]}을 저장한다(요일이 있는 작품만). 바뀐 게 없으면 쓰지 않는다 — 목록을 열 때마다 큰 값을 다시 쓰지 않게."""
    wanted = {title_id: days for title_id, days in days_by_id.items() if days}
    if wanted != _live_map():
        repository.set_setting(NAVER_MAP_KEY, json.dumps(wanted, ensure_ascii=False, sort_keys=True))


# ── 요일별 목록에 없는 작품(장기 휴재 등)의 요일: 작품 페이지에서 읽기 ───────────────────────────────

LOOKUP_KEY = "naver_weekday_lookup"
LOOKUP_FRESH_DAYS = 60  # 알아낸 요일은 이만큼 지나면 다시 확인한다
LOOKUP_EMPTY_RETRY_DAYS = 7  # 페이지에 요일이 없었던 작품(완결 등)은 이만큼 지나면 다시
LOOKUP_FAILED_RETRY_DAYS = 1  # 요청이 실패했으면 하루 뒤에 다시
LOOKUP_BATCH = 40  # 한 번에 알아볼 최대 작품 수
LOOKUP_PAUSE_SECONDS = 0.3  # 작품 사이에 쉬는 시간(네이버에 한꺼번에 몰리지 않게)
_KO_DAYS = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat", "일": "sun"}


def parse_title_page(html: str) -> list[str]:
    """네이버 모바일 작품 페이지(m.comic.naver.com/webtoon/list)에서 연재 요일을 읽는다(실제 HAR 캡처로 확인한 구조).
    ① 작품 정보의 "연재" 항목: <div class="week_day"> … <ul class="list_detail"><li>토</li></ul> — 여러 요일이면 <li>가 여러 개. ② 없으면 상태 스크립트의 weekdayTabQueryParam: 'sat'.
    요일이 아닌 값(완결 등)만 있거나 둘 다 없으면 빈 목록."""
    block = re.search(r'<div class="week_day">(.*?)</div>', html, re.S)
    if block:
        listing = re.search(r'<ul class="list_detail">(.*?)</ul>', block.group(1), re.S)
        found = {_KO_DAYS[day] for day in re.findall(r"<li>\s*([월화수목금토일])\s*</li>", listing.group(1))} if listing else set()
        if found:
            return [day for day in DAYS if day in found]
    tab = re.search(r"weekdayTabQueryParam:\s*'(mon|tue|wed|thu|fri|sat|sun)'", html)
    return [tab.group(1)] if tab else []


def _get_lookup() -> dict[str, dict]:
    raw = repository.get_setting(LOOKUP_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_lookup(title_id: str, days: list[str] | None, now: float | None = None) -> None:
    """작품 하나를 알아본 결과를 저장한다. days=None이면 요청이 실패한 것(요일이 없는 것과 구분해서 하루 뒤에 다시 시도)."""
    lookup = _get_lookup()
    lookup[title_id] = {"ok": days is not None, "days": days or [], "at": now if now is not None else time.time()}
    repository.set_setting(LOOKUP_KEY, json.dumps(lookup, ensure_ascii=False))


def pending_lookup_ids(title_ids: list[str], now: float | None = None) -> list[str]:
    """title_ids 중 지금 (다시) 알아봐야 할 작품 — 입력 순서 유지. 알아낸 요일은 60일, 요일 없음은 7일, 실패는 1일 동안 다시 묻지 않는다."""
    now = now if now is not None else time.time()
    lookup = _get_lookup()
    pending = []
    for title_id in title_ids:
        entry = lookup.get(title_id)
        if entry is None:
            pending.append(title_id)
            continue
        keep_days = (LOOKUP_FRESH_DAYS if entry.get("days") else LOOKUP_EMPTY_RETRY_DAYS) if entry.get("ok") else LOOKUP_FAILED_RETRY_DAYS
        if now - entry.get("at", 0) >= keep_days * 86400:
            pending.append(title_id)
    return pending
