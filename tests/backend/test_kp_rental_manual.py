"""수동 다운로드 API의 대여권 — 분석 표에 "대여권" 상태가 나오고, 확인 값(use_rental_tickets)이 있을 때만 쓴다.

대여권은 기다무로 못 여는 잠긴 회차에만 쓸 수 있고, 보유 대여권이 있을 때만 고를 수 있다. 확인 값이 없으면 예전처럼 건너뛴다.
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import aiohttp
import httpx

from app import db, repository
from app import kakao_page_auth as auth
from app import kakao_page_download as kp
from app import kakao_cover
from fake_kakao_rental_site import JPG, SITE, FakeSession, Site, item
db.get_connection()
import app.main as m

aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
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
        # Scenario 1: 상태가 이유별로 나뉜다 — 기다무로 열 수 있는데 충전 중이면 "waitfree_wait", 기다무로 못 열고 대여권이 있으면 "rental",
        #   숨김은 그냥 "locked"(선택 불가는 모두 같다)
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True), item(4, blocked=True), item(5, blocked=True, hidden=True)], rental=2, waitfree=False)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a) == {1: ("free", True), 2: ("waitfree_wait", False), 3: ("rental", True), 4: ("rental", True), 5: ("locked", False)}, states(a)
        assert a["tickets"]["rental_count"] == 2
        # Scenario 2: 기다무로 못 여는데 보유 대여권이 없으면 "rental_needed"(선택 불가)
        SITE["site"] = Site([item(1, free=True), item(2, blocked=True)], rental=0, ticket_type=None)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a)[2] == ("rental_needed", False)
        # Scenario 3: 기다무가 없는 작품은 잠긴 회차가 곧 대여권 대상 — 대여권이 있으면 "rental", 없으면 "rental_needed"
        SITE["site"] = Site([item(1, free=True), item(2)], rental=1, series_waitfree=False)
        assert states((await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json())[2] == ("rental", True)
        SITE["site"] = Site([item(1, free=True), item(2)], rental=0, ticket_type=None, series_waitfree=False)
        assert states((await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json())[2] == ("rental_needed", False)
        # Scenario 3-2: 기다무가 충전돼 있으면 기다무로 열 수 있는 회차는 "waitfree"(선택 가능)
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=0, ticket_type=None, waitfree=True)
        assert states((await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()) == {1: ("free", True), 2: ("waitfree", True), 3: ("rental_needed", False)}
        # Scenario 3-3: "N일 후 무료"로 열릴 회차는 기다무/대여권을 기다리는 게 아니라 날짜를 기다리므로 그냥 "locked"
        SITE["site"] = Site([item(1, free=True), item(2, free_dt="2099-01-01T00:00:00+09:00"), item(3, blocked=True, free_dt="2099-01-01T00:00:00+09:00")], rental=0, ticket_type=None)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a)[2] == ("locked", False) and states(a)[3] == ("locked", False) and a["episodes"][1]["free_at"] == "2099-01-01"
        # Scenario 3-4: 이용권 조회가 안 되면(로그아웃/서버 오류) 기다무/대여권 사정을 모르므로 이유를 단정하지 않고 "locked"
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True)], ticket_my_fail=True)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert a["tickets"] is None and states(a) == {1: ("free", True), 2: ("locked", False), 3: ("locked", False)}, states(a)
        # Scenario 3-5: 이미 받은 회차는 지금 잠겨 있어도(대여가 끝남) 기다릴 게 없으므로 이유 표시 없이 "locked"
        folder = Path(kp.series_folder(repository.get_setting(kp.DOWNLOAD_ROOT_SETTING_KEY), "작품"))
        folder.mkdir(parents=True, exist_ok=True)
        for n in (1, 2): (folder / f"{n:04d}_{n}화.zip").write_bytes(b"x")
        SITE["site"] = Site([item(1, free=True), item(2), item(3), item(4, blocked=True)], rental=0, ticket_type=None, waitfree=False)
        a = (await c.get("/api/kakao-manual/analyze", params={"series_id": 1})).json()
        assert states(a) == {1: ("free", True), 2: ("locked", False), 3: ("waitfree_wait", False), 4: ("rental_needed", False)}, states(a)
        for f in folder.glob("*.zip"): f.unlink()
        print("1~3) 분석 표의 상태 구분 OK (기다무/대여권/기다무 대기/대여권 필요/잠금)")

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

        # Scenario 6: 이미 대여 중인 회차(46,47: 직접 대여권을 써서 연 것)와 아직 안 연 회차(48: 기다무 불가)를 함께 골랐을 때
        #   확인 값 없음 → 대여 중인 두 회차만 받고 48은 건너뜀(옛 화면이 보내는 요청과 같다) / 확인 값 있음 → 48도 대여권 1장으로 열어 받는다
        for flag in (False, True):
            SITE["site"] = site = Site([item(46, free=False), item(47, free=False), item(48, blocked=True), item(49, blocked=True)], rental=3)
            site.opened.update({5046, 5047})          # 이미 대여 중
            body = {"series_id": 1, "numbers": [46, 47, 48]}
            if flag: body["use_rental_tickets"] = True
            assert (await c.post("/api/kakao-manual/run", json=body)).status_code == 200
            log_text = "\n".join((await wait_manual(c))["log"])
            assert "✅ 46번 회차" in log_text and "✅ 47번 회차" in log_text, log_text      # 대여 중인 회차는 어느 쪽이든 받는다(두 번째 반복은 이미 있어서 "교체")
            if flag:
                assert "48번 회차를 대여권으로 열었습니다" in log_text and "✅ 48번 회차" in log_text, log_text
                assert [p[1] for p in use_posts(site)] == [{"product_id": 5048, "ticket_type": "RT01"}] and site.rental == 2
            else:
                assert "48번 회차: 기다무로 열 수 없는 회차입니다 — 건너뜀" in log_text, log_text   # 옛 화면이 보내는 요청의 결과(스크린샷과 같은 로그)
                assert use_posts(site) == [] and site.rental == 3
        print("6) 대여 중인 회차 + 대여권 회차 함께 선택 OK")

asyncio.run(main())
print("ALL OK")
