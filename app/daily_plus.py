"""네이버 웹툰 '매일+' 작품 구분 — 전체목록/구독해제/제외됨/수동 다운로드 검색에서 숨기는 데 쓴다.

요일별 전체 목록 API나 통합검색 응답에는 매일+를 가리키는 값이 확인되지 않아서, 모바일 매일+ 페이지
(m.comic.naver.com/webtoon/weekday?week=dailyPlus)의 본문 목록 링크가 `/webtoon/list?titleId=N&week=dailyPlus`로 붙는 것을 쓴다(실제 캡처로 확인).
같은 페이지의 '이달의 신작' 캐러셀 링크에는 week가 없어서 매일+ 작품으로 세지 않는다.
하루에 한 번만 받아 DB(settings)에 담아 두고, 받기에 실패하면 예전 기록을 쓴다. 한 번도 못 받았으면 아무것도 숨기지 않는다(일반 작품이 사라지는 사고를 막으려고).
"""
import json
import logging
import re
from datetime import datetime, timedelta

import aiohttp

from app import repository

log = logging.getLogger(__name__)

DAILY_PLUS_URL = "https://m.comic.naver.com/webtoon/weekday?week=dailyPlus"
CACHE_KEY = "naver_daily_plus_ids"  # JSON {"fetched_at": ISO 시각, "ids": [titleId, ...]}
_MAX_AGE = timedelta(hours=24)
_LINK_RE = re.compile(r"/webtoon/list\?titleId=(\d+)&(?:amp;)?week=dailyPlus")
_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def parse_ids(html: str) -> set[str]:
    return set(_LINK_RE.findall(html))


async def fetch_ids(session: aiohttp.ClientSession, timeout_seconds: int) -> set[str] | None:
    """매일+ 작품의 titleId 모음. 못 받았거나 페이지 구조가 바뀌어 하나도 못 찾으면 None(기존 기록을 지우지 않게)."""
    try:
        async with session.get(DAILY_PLUS_URL, headers=_MOBILE_HEADERS, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as response:
            if response.status != 200:
                log.error("매일+ 목록 조회 실패: HTTP %s", response.status)
                return None
            html = await response.text()
    except Exception as e:
        log.error("매일+ 목록 조회 예외: %s", e)
        return None
    return parse_ids(html) or None


def _load() -> dict | None:
    raw = repository.get_setting(CACHE_KEY)
    try:
        stored = json.loads(raw) if raw else None
    except ValueError:
        return None
    return stored if isinstance(stored, dict) else None


def cached_ids() -> set[str]:
    return set((_load() or {}).get("ids") or [])


def _is_fresh(stored: dict | None) -> bool:
    try:
        return stored is not None and datetime.now() - datetime.fromisoformat(stored["fetched_at"]) < _MAX_AGE
    except (KeyError, ValueError):
        return False


async def current_ids(timeout_seconds: int, session: aiohttp.ClientSession | None = None) -> set[str]:
    """숨길 매일+ titleId 모음. 기록이 하루 안이면 그대로, 아니면 새로 받고(실패하면 예전 기록)."""
    if _is_fresh(_load()):
        return cached_ids()
    if session is None:
        async with aiohttp.ClientSession() as own_session:
            fetched = await fetch_ids(own_session, timeout_seconds)
    else:
        fetched = await fetch_ids(session, timeout_seconds)
    if fetched is None:
        return cached_ids()
    repository.set_setting(CACHE_KEY, json.dumps({"fetched_at": datetime.now().isoformat(), "ids": sorted(fetched)}))
    return fetched
