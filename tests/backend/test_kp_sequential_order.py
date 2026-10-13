"""자동 다운로드는 회차를 번호순으로 처리한다 — 기다무로 여는 잠긴 회차도 자기 번호 차례에 열고, 뒤쪽의 열려 있는 회차로 먼저 뛰지 않는다.

예전에는 "받을 수 있는 회차를 전부 받은 뒤에 첫 잠긴 회차를 기다무로 열기"여서, 7번이 잠겨 있고 15~22번이 열려 있으면 15번으로 먼저 점프했다.
지금 못 여는 잠긴 회차(기다무가 충전 중이거나 기다무로 못 여는 회차)는 건너뛰고 뒤의 열린 회차를 받는다(후기처럼 뒤에 무료가 있어도 영원히 막히지 않게).
받기에 실패하면 번호순을 지키려고 그 회차에서 멈춘다(기존과 같음).
"""
import asyncio
import tempfile
from pathlib import Path

from app import db
db.get_connection()
from app import kakao_page_download as kp
from app import kakao_cover
from fake_kakao_rental_site import JPG, Site, item

_orig = asyncio.sleep
async def _ns(x): await _orig(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
client = lambda site: kp.KakaoPageClient(site, {}, 10)
use_posts = lambda site: [p for p in site.posts if p[0].endswith("ticket/use")]


async def main():
    root = lambda: str(Path(tempfile.mkdtemp()))
    run = lambda site, **k: kp.run_download(client(site), series_id=1, title=None, download_root=root(), **({"max_episodes": 20} | k))

    # Scenario 1: 2번이 잠겨 있고 기다무가 충전돼 있으면 1 → (2를 기다무로 열어) 2 → 3 순서로 받는다(예전에는 1, 3, 2)
    site = Site([item(1, free=True), item(2), item(3, free=True)], waitfree=True)
    r = await run(site)
    assert r.downloaded == [1, 2, 3] and r.ticket_used == 2 and r.failed is None, (r.downloaded, r.ticket_used)
    assert [p[1]["product_id"] for p in use_posts(site)] == [5002]
    # Scenario 2: 기다무가 아직 충전 중이면 잠긴 2번은 건너뛰고(멈추지 않고) 뒤의 열린 3번을 받는다
    site = Site([item(1, free=True), item(2), item(3, free=True)], waitfree=False)
    r = await run(site)
    assert r.downloaded == [1, 3] and r.ticket_used is None and r.failed is None and use_posts(site) == []
    # Scenario 3: 기다무로 못 여는 최신 회차(blocked)가 가장 앞의 잠긴 회차여도 기다무를 시도하지 않고 열린 회차는 받는다(후기처럼 뒤에 무료가 있어도 막히지 않음)
    site = Site([item(1, free=True), item(2, blocked=True), item(3, free=True)], waitfree=True)
    r = await run(site)
    assert r.downloaded == [1, 3] and r.ticket_used is None and use_posts(site) == []
    # Scenario 4: 작품당 상한은 번호순으로 센다 — 상한 2이면 1과 기다무로 연 2번까지(3번이 아니라)
    site = Site([item(1, free=True), item(2), item(3, free=True), item(4, free=True)], waitfree=True)
    r = await run(site, max_episodes=2)
    assert r.downloaded == [1, 2] and r.ticket_used == 2
    # Scenario 5: 기다무로 연 회차를 받다가 실패하면 번호순을 지키려고 거기서 멈춘다(뒤의 열린 3번은 받지 않는다)
    site = Site([item(1, free=True), item(2), item(3, free=True)], waitfree=True, no_image=(5002,))
    r = await run(site)
    assert r.downloaded == [1] and r.ticket_used == 2 and r.failed == 2, (r.downloaded, r.ticket_used, r.failed)
    # Scenario 6: 열린 회차 받기에 실패하면 거기서 멈춘다 — 기다무도 쓰지 않는다(기존과 같음)
    site = Site([item(1, free=True), item(2), item(3, free=True)], waitfree=True, no_image=(5001,))
    r = await run(site)
    assert r.downloaded == [] and r.failed == 1 and use_posts(site) == []
    # Scenario 7: 도굴왕 상황 — 6번까지 받았고 7~14번이 잠겨 있고(기다무 가능), 15~22번도 대여가 끝나 잠겨 있으면, 15번이 아니라 7번을 기다무로 연다
    items = [item(n, free=True) for n in range(1, 7)] + [item(n) for n in range(7, 23)] + [item(n, blocked=True) for n in range(23, 27)]
    site = Site(items, waitfree=True)
    r = await run(site)
    assert r.downloaded == [1, 2, 3, 4, 5, 6, 7] and r.ticket_used == 7, (r.downloaded, r.ticket_used)
    assert [p[1]["product_id"] for p in use_posts(site)] == [5007]
    print("ALL OK")

asyncio.run(main())
