"""빠른 상태 스캔 — 완결/휴재/요일만 가볍게 확인한다(작가/태그 등록 같은 무거운 일은 하지 않는다. 그건 "지금 전체 재동기화").

대상: 완결이 아닌 추적 작품 중 **요일별 목록(네이버)/요일 목록(카카오)에 없어서 이 앱이 직접 확인해야 하는 것**(장기 휴재, 판매 중지 등). 목록에 있는 작품은 목록이 이미 최신이다.
매일+로 숨긴 작품과 이미 완결인 작품은 건너뛴다. 순서는 **요일을 모르는 작품("기타")을 먼저**, 그다음 오늘 요일부터 돌아가며 요일별로 차례로.
최근에 확인한 작품은 건너뛰어서(실패한 것은 15분 뒤 다시) 버튼을 눌러도 매번 전부 다시 하지 않는다. 네이버는 작품 정보 API 한 번(요일/완결/휴재),
카카오는 상태 확인 + (요일을 모르는 연재/휴재만) 개요이고, 카카오는 한꺼번에 몰리지 않게 40개마다 쉰다. 한 작품이 실패해도 나머지는 계속한다.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

from app import daily_plus, kakao_catalog, kakao_page_download, kakao_status, naver_api, repository, tracker, weekday_lookup, weekdays
from app.config import Settings
from app.kakao_filters import is_excluded_title
from app.models import TitlePage

log = logging.getLogger(__name__)

WEEK = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NAVER_PAUSE_SECONDS = 0.15  # 네이버 작품 사이에 쉬는 시간
KAKAO_PAUSE_SECONDS = 0.3  # 카카오 작품 사이에 쉬는 시간
KAKAO_BATCH = 40  # 카카오는 이만큼 확인할 때마다
KAKAO_BATCH_REST_SECONDS = 5  # 이만큼 길게 쉰다(몰리지 않게)
FAILED_RETRY_SECONDS = 15 * 60  # 실패한 확인은 이만큼 지나야 다시 한다
PROGRESS_EVERY = 25  # 이만큼 확인할 때마다 진행 상황을 알린다
_SEOUL = ZoneInfo("Asia/Seoul")


@dataclass
class Target:
    platform: str  # "naver" | "kakao"
    title_id: str | int
    title: str
    group: str  # "etc"(요일을 모름) 또는 mon~sun


@dataclass
class ScanStats:
    scanned: int = 0
    failed: int = 0
    resolved_days: int = 0  # 요일을 몰랐다가 알아낸 작품
    newly_finished: int = 0  # 새로 완결/서비스 종료/판매 중지로 확인된 작품
    skipped_fresh: int = 0
    by_group: dict[str, int] = field(default_factory=dict)


def today_key(now: datetime | None = None) -> str:
    return WEEK[(now or datetime.now(_SEOUL)).astimezone(_SEOUL).weekday()]


def group_order(today: str) -> list[str]:
    """처리 순서 — 요일을 모르는 작품(etc)을 먼저, 그다음 오늘부터 돌아가며."""
    start = WEEK.index(today)
    return ["etc"] + [WEEK[(start + offset) % 7] for offset in range(7)]


def _group_of(days: list[str], order: list[str]) -> str:
    """알고 있는 요일 중 처리 순서가 가장 빠른 요일(오늘 이후 가장 가까운 요일), 모르면 etc."""
    known = [day for day in order[1:] if day in days]
    return known[0] if known else "etc"


def _is_fresh(at: float | None, ok: bool, max_age_seconds: float, now: float) -> bool:
    """최근에 확인했는가 — 성공한 확인은 max_age 안이면, 실패한 확인은 FAILED_RETRY_SECONDS 안이면 최근으로 본다."""
    if at is None:
        return False
    return now - at < (max_age_seconds if ok else FAILED_RETRY_SECONDS)


def _naver_targets(order: list[str], max_age: float, now: float) -> tuple[list[Target], int]:
    """확인할 네이버 작품과 최근 확인해서 건너뛴 수. 요일별 목록(live)에 있는 작품은 목록이 최신이고, 매일+ 숨김/완결은 대상이 아니다."""
    live = {title_id for title_id, days in weekdays._LIVE_MAP.load().items() if days}
    hidden = daily_plus.cached_ids()
    known_days = weekdays.get_naver_map()
    lookups = weekdays._get_lookup()
    targets: list[Target] = []
    skipped = 0
    for wt in repository.list_all():
        if wt.is_finished or wt.title_id in live or wt.title_id in hidden:
            continue
        entry = lookups.get(wt.title_id)
        if entry is not None and _is_fresh(entry.get("at"), bool(entry.get("ok")), max_age, now):
            skipped += 1
            continue
        targets.append(Target("naver", wt.title_id, wt.title, _group_of(known_days.get(wt.title_id, []), order)))
    return targets, skipped


def _kakao_targets(order: list[str], max_age: float, now: float) -> tuple[list[Target], int]:
    """확인할 카카오 작품과 건너뛴 수. 요일 목록 캐시에 있는 작품은 이미 최신이고, 완결/서비스 종료/판매 중지로 확인된 작품은 대상이 아니다."""
    listed = {item["title_id"] for item in kakao_catalog.snapshot()[0] if item.get("weekdays")}
    hidden = kakao_status.hidden_ids()
    lookups = kakao_status.get_lookup()
    targets: list[Target] = []
    skipped = 0
    for row in repository.get_kakao_webtoons_map().values():
        series_id = row["title_id"]
        if row["is_finished"] or series_id in listed or series_id in hidden or is_excluded_title(row["title"]):  # 제목에 "단행본"이 있는 작품은 확인하지 않는다
            continue
        entry = lookups.get(str(series_id))
        if entry is not None:
            ok = entry.get("on_issue") is not None or kakao_status.is_ended(entry)
            if _is_fresh(entry.get("at"), ok, max_age, now):
                skipped += 1
                continue
        targets.append(Target("kakao", series_id, row["title"], _group_of(kakao_status.weekdays_of(series_id), order)))
    return targets, skipped


def collect_targets(*, today: str, max_age_hours: float, scope: set[str] | None = None, now: float | None = None) -> tuple[list[Target], int]:
    """(확인할 작품 목록(처리 순서대로), 최근에 확인해서 건너뛴 수). scope를 주면 그 그룹(etc/mon~sun)만."""
    now = now if now is not None else time.time()
    max_age = max_age_hours * 3600
    order = group_order(today)
    naver, naver_skipped = _naver_targets(order, max_age, now)
    kakao, kakao_skipped = _kakao_targets(order, max_age, now)
    targets = naver + kakao
    if scope is not None:
        targets = [t for t in targets if t.group in scope]
    rank = {group: index for index, group in enumerate(order)}
    targets.sort(key=lambda t: (rank[t.group], t.platform != "naver", t.title))  # 그룹 순서 → 네이버 먼저 → 이름순
    return targets, naver_skipped + kakao_skipped


async def scan(targets: list[Target], settings: Settings, progress: Callable[[str], None] | None = None) -> ScanStats:
    """targets를 차례로 확인해 결과를 저장한다. 한 작품이 실패(예외 포함)해도 나머지는 계속한다."""
    stats = ScanStats()
    if not targets:
        return stats
    total = len(targets)
    kakao_done = 0
    listed = {item["title_id"] for item in kakao_catalog.snapshot()[0] if item.get("weekdays")}
    async with aiohttp.ClientSession() as naver_session, kakao_page_download.new_session() as kakao_session:
        kakao_client = kakao_page_download.client_or_anonymous(kakao_session, settings.request_timeout_seconds)
        for done, target in enumerate(targets, 1):
            if target.platform == "naver":
                ok, found_days, newly = await _scan_naver(naver_session, target, settings.request_timeout_seconds)
                await asyncio.sleep(NAVER_PAUSE_SECONDS)
            else:
                ok, found_days, newly = await _scan_kakao(kakao_client, target, listed)
                kakao_done += 1
                await asyncio.sleep(KAKAO_PAUSE_SECONDS)
                if kakao_done % KAKAO_BATCH == 0 and done < total:
                    await asyncio.sleep(KAKAO_BATCH_REST_SECONDS)
            stats.scanned += 1
            stats.failed += 0 if ok else 1
            stats.resolved_days += 1 if found_days else 0
            stats.newly_finished += 1 if newly else 0
            stats.by_group[target.group] = stats.by_group.get(target.group, 0) + 1
            if progress is not None and (done % PROGRESS_EVERY == 0 or done == total):
                progress(f"빠른 상태 스캔 진행 {done}/{total}개")
    return stats


async def _scan_naver(session: aiohttp.ClientSession, target: Target, timeout_seconds: int) -> tuple[bool, bool, bool]:
    """(확인 성공, 요일을 새로 알아냄, 새로 완결) — 작품 정보 API(요일/완결/휴재, 필요하면 모바일 페이지 폴백) 결과를 저장한다."""
    title_id = str(target.title_id)
    tracked = repository.get(title_id)
    had_days = bool(weekdays.get_naver_map().get(title_id))
    try:
        page = await naver_api.fetch_title_weekdays(session, title_id, timeout_seconds)
    except Exception as e:
        log.warning("빠른 상태 스캔 실패 (titleId=%s): %s", title_id, e)
        page = TitlePage(status=None)
    await asyncio.to_thread(weekday_lookup.apply_page, title_id, page)
    newly_finished = bool(page.finished) and tracked is not None and not tracked.is_finished
    return page.status == 200, (not had_days) and bool(page.days), newly_finished


async def _scan_kakao(client, target: Target, listed: set[int]) -> tuple[bool, bool, bool]:
    series_id = int(target.title_id)
    had_days = bool(kakao_status.weekdays_of(series_id))
    was_hidden = kakao_status.is_hidden(kakao_status.get_lookup().get(str(series_id), {}))
    resolved = await tracker.refresh_one_kakao_status(client, series_id, listed)
    now_hidden = kakao_status.is_hidden(kakao_status.get_lookup().get(str(series_id), {}))
    return bool(resolved), (not had_days) and bool(kakao_status.weekdays_of(series_id)), now_hidden and not was_hidden
