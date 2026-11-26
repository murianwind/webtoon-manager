"""기다무 다운로드 예정 목록의 "남은 회차" — 작품마다 기다무로 열어야 하는 남은 회차 수, 대여권으로 받아야 하는 남은 회차 수, 충전 주기를 다운로드할 때 저장해 두고
(기다무는 충전될 때마다 한 회차씩 열린다) 마지막 기다무 회차를 받는 시각 = 받기 시작하는 시각 + (남은 기다무 회차 - 1) × 충전 주기 를 화면용으로 계산한다.
- 센다: 잠긴 회차 중 기다무 작품이면서 기다무로 열 수 있는 회차 = 기다무 남은 회차 / 기다무로 못 여는 회차(기다무 없는 작품 포함) = 대여권 남은 회차.
  기다리면 열리는 회차(숨김, "N일 후 무료")와 이번 실행에서 막 연 회차는 세지 않는다.
- 주기: 작품 정보의 값이 있으면 그것, 없으면 이용권 응답의 값, 둘 다 모르면(0) 모른다."""
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

import httpx

from app import db, kakao_cover, repository, waitfree_sync
db.get_connection()
from app import kakao_page_download as kp
from app.kakao_page_download.models import Episode, count_remaining_locked
from fake_kakao_rental_site import JPG, Site, item
import app.main as m

_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG

def ep(n, **k):
    return Episode(product_id=5000 + n, title=f"{n}화", number=n, subtitle=f"{n}화", is_free=False, accessible=k.get("accessible", False), page_count=2, hidden=k.get("hidden", False),
                   waitfree_blocked=k.get("blocked", False), free_change_dt=k.get("free_dt"))

# Scenario 1: 세는 규칙 — 기다무 회차 / 대여권 회차 / 세지 않는 회차(열려 있음·숨김·곧 무료·이번에 막 연 회차)
wf_series = {"is_waitfree": True}
future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
locked = [ep(2), ep(3), ep(4, blocked=True), ep(5, blocked=True), ep(6, hidden=True), ep(7, free_dt=future), ep(8, accessible=True), ep(9)]
assert count_remaining_locked(locked, wf_series, set()) == (3, 2), count_remaining_locked(locked, wf_series, set())
assert count_remaining_locked(locked, wf_series, {2, 5}) == (2, 1), "막 연 회차(기다무 2번, 대여권 5번)는 센 뒤 뺀다"
assert count_remaining_locked(locked, {"is_waitfree": False}, set()) == (0, 5), "기다무가 없는 작품은 열 수 없는 잠긴 회차가 모두 대여권 회차(숨김·곧 무료·열린 회차는 제외)"
assert count_remaining_locked([], wf_series, set()) == (0, 0)

# Scenario 2: 다운로드 실행이 끝나면 남은 회차와 주기를 저장한다
def run(series_id, site, **kw):
    repository.upsert_new_kakao_webtoon(series_id, "작품"); waitfree_sync.remember(series_id, "2099-01-01T00:00:00+09:00")
    client = kp.KakaoPageClient(site, {}, 10)
    asyncio.run(kp.run_download(client, series_id=series_id, title=None, download_root=tempfile.mkdtemp(), max_episodes=10, use_rental_tickets=kw.get("rental", False)))
    w = repository.get_kakao_webtoon(series_id)
    return w["waitfree_left"], w["rental_left"], w["waitfree_period_minutes"]

free = lambda n: item(n, free=True)
items = [free(1), item(2), item(3), item(4), item(5, blocked=True), item(6, blocked=True)]
assert run(1, Site(items, waitfree=True)) == (2, 2, 1440), "충전돼 있어 2번을 기다무로 열었다 → 기다무 남은 3·4, 대여권 5·6, 주기는 이용권 응답의 값"
assert run(2, Site(items, waitfree=False)) == (3, 2, 1440), "충전 전이면 못 열었으니 2·3·4가 남는다"
assert run(3, Site(items, waitfree=False, series_period=720)) == (3, 2, 720), "작품 정보의 주기가 있으면 그것을 쓴다"
assert run(4, Site(items, waitfree=False, series_period=720), rental=True) == (3, 0, 720), "대여권 자동 사용으로 5·6을 열어 받았으면 대여권 남은 회차는 0"
assert run(5, Site([free(1), item(2), item(3)], waitfree=False, series_waitfree=False)) == (0, 2, 0), "기다무가 없는 작품: 기다무 0, 대여권 회차 2, 주기 모름(0)"
assert run(6, Site([free(1), free(2)])) == (0, 0, 0), "잠긴 회차가 없으면 둘 다 0"

# Scenario 3: 목록 화면용 계산 — 마지막 기다무 회차를 받는 시각
def seed(series_id, next_at, wf, rental, period):
    repository.upsert_new_kakao_webtoon(series_id, "작품"); waitfree_sync.remember(series_id, next_at)
    repository.set_kakao_waitfree_remaining(series_id, wf, rental, period)
    return next(i for i in waitfree_sync.schedule_view() if i["title_id"] == series_id)

NEXT = "2026-11-26T12:01:00+00:00"
v = seed(10, NEXT, 10, 2, 1440)
due = datetime.fromisoformat(v["due_at"])
assert (v["waitfree_left"], v["rental_left"]) == (10, 2) and datetime.fromisoformat(v["last_at"]) == due + timedelta(days=9), v
v = seed(11, NEXT, 1, 0, 0); assert v["last_at"] == v["due_at"], "한 회차 남았으면 주기를 몰라도 받기 시작하는 시각이 마지막"
v = seed(12, NEXT, 3, 0, 0); assert v["last_at"] is None and v["waitfree_left"] == 3, "여러 회차인데 주기를 모르면 마지막 시각은 모른다"
v = seed(13, NEXT, 0, 2, 1440); assert v["last_at"] is None and v["waitfree_left"] == 0 and v["rental_left"] == 2, "기다무 회차가 없으면 마지막 시각 없음"
repository.upsert_new_kakao_webtoon(15, "작품"); waitfree_sync.remember(15, NEXT)
v = next(i for i in waitfree_sync.schedule_view() if i["title_id"] == 15)
assert v["waitfree_left"] is None and v["rental_left"] is None and v["last_at"] is None, "아직 한 번도 다운로드하지 않아 모르면 전부 None"

# Scenario 4: 설정 API가 그대로 내려준다
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        data = (await c.get("/api/waitfree/schedule")).json()
        by_id = {i["title_id"]: i for i in data["items"]}
        assert by_id[10]["waitfree_left"] == 10 and by_id[10]["rental_left"] == 2 and by_id[10]["last_at"], by_id[10]
        assert by_id[15]["waitfree_left"] is None and by_id[15]["last_at"] is None, by_id[15]
asyncio.run(api())
print("ALL OK")
