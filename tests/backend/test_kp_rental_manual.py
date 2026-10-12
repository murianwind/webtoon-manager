"""수동 다운로드 API의 대여권 — 분석 표에 "대여권" 상태가 나오고, 확인 값(use_rental_tickets)이 있을 때만 쓴다.

대여권은 기다무로 못 여는 잠긴 회차에만 쓸 수 있고, 보유 대여권이 있을 때만 고를 수 있다. 확인 값이 없으면 예전처럼 건너뛴다.
"""
import asyncio
import json
import tempfile
import time

import aiohttp
import httpx

from app import db, repository
from app import kakao_page_auth as auth
from app import kakao_page_download as kp
from fake_kakao_rental_site import JPG, SITE, FakeSession, Site, item
db.get_connection()
import app.main as m

aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
kp.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
use_posts = lambda site: [p for p in site.posts if p[0].endswith("ticket/use")]


async def wait_manual(c, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = (await c.get("/api/jobs/status")).json().get("manual") or {}
        if st.get("status") and st["status"] != "running": return st
        await _o(0.02)
    raise AssertionError("수동 다운로드가 안 끝남")


async def main():
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, tempfile.mkdtemp())
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])))
    states = lambda a: {e["number"]: (e["state"], e["selectable"]) for e in a["episodes"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 1: 보유 대여권이 있으면 기다무로 못 여는 잠긴 회차가 "rental"(선택 가능)이고, 기다무로 열 수 있는 회차(충전 대기)는 그대로 잠김
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True), item(4, blocked=True), item(5, blocked=True, hidden=True)], rental=2, waitfree=False)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a) == {1: ("free", True), 2: ("locked", False), 3: ("rental", True), 4: ("rental", True), 5: ("locked", False)}, states(a)
        assert a["tickets"]["rental_count"] == 2
        # Scenario 2: 보유 대여권이 없으면 "잠김"(선택 불가)
        SITE["site"] = Site([item(1, free=True), item(2, blocked=True)], rental=0, ticket_type=None)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a)[2] == ("locked", False)
        # Scenario 3: 기다무가 없는 작품은 잠긴 회차가 곧 대여권 대상
        SITE["site"] = Site([item(1, free=True), item(2)], rental=1, series_waitfree=False)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a)[2] == ("rental", True)
        print("1~3) 분석 표의 대여권 상태 OK")

        # Scenario 4: 확인 값이 없으면(기본) 고른 대여권 회차도 쓰지 않고 건너뛴다
        SITE["site"] = site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True)], rental=2)
        assert (await c.post("/api/kakao-manual/run", json={"series_id": 1, "numbers": [1, 2, 3]})).status_code == 200
        await wait_manual(c)
        assert use_posts(site) == [], site.posts
        # Scenario 5: 확인 값이 있으면 고른 회차를 보유 수까지 쓴다 — 받은 회차는 이력에 남고, 진행 로그에 남은 장수가 나온다
        SITE["site"] = site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2)
        assert (await c.post("/api/kakao-manual/run", json={"series_id": 1, "numbers": [2, 3, 4], "use_rental_tickets": True})).status_code == 200
        st = await wait_manual(c)
        assert [p[1]["product_id"] for p in use_posts(site)] == [5002, 5003] and site.rental == 0, site.posts
        log_text = "\n".join(st["log"]) if isinstance(st.get("log"), list) else str(st)
        assert "대여권으로 열었습니다" in log_text and "대여권 2장" in log_text, log_text
        print("4~5) 수동 실행 OK — 확인 값 없으면 건너뜀, 있으면 보유 수까지")

asyncio.run(main())
print("ALL OK")
