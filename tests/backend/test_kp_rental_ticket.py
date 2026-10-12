"""일반 대여권(RT01) 사용 — 실제 캡처(대여권.har)의 응답 형태를 따른다.

- ready_to_use: available.ticket_rental_type "RT01", my.ticket_rental_count(작품별 보유 장수), single.waitfree_block, series.is_waitfree
- ticket/use: POST product_id + ticket_type=RT01 → 성공하면 그 회차가 대여 중(RAT2)이 된다
- 쓰는 곳은 "기다무로 못 여는 회차"뿐(기다무 불가 회차이거나 기다무가 없는 작품). 결제/구매 주소는 코드에 없다.
"""
import asyncio
import inspect
import tempfile
from pathlib import Path

from app import db
db.get_connection()
from app import kakao_page_download as kp
from fake_kakao_rental_site import JPG, Site, item

_orig = asyncio.sleep
async def _ns(x): await _orig(0)
kp.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG


client = lambda site: kp.KakaoPageClient(site, {}, 10)
use_posts = lambda site: [p for p in site.posts if p[0].endswith("ticket/use")]


async def client_level():
    # Scenario 1: 기다무로 못 여는 회차 + RT01 + 보유 3장 → 올바른 본문으로 한 번만 쓰고, 남은 장수를 돌려준다
    site = Site([item(1, blocked=True)])
    ok, reason, remaining = await client(site).use_rental_ticket(5001)
    assert ok and remaining == 2 and site.rental == 2, (ok, reason, remaining)
    assert site.posts == [("v1/ticket/use", {"product_id": 5001, "ticket_type": "RT01"})], site.posts
    # Scenario 2: 보유 0장이면 쓰지 않는다
    site = Site([item(1, blocked=True)], rental=0, ticket_type=None)
    ok, reason, remaining = await client(site).use_rental_ticket(5001)
    assert not ok and site.posts == [] and "대여권" in reason, reason
    # Scenario 3: 서버가 알려 주는 이용권이 RT01이 아니면(선물권 RT06 등) 쓰지 않는다
    for kind in ("RT06", "RT05", None):
        site = Site([item(1, blocked=True)], ticket_type=kind)
        ok, reason, _ = await client(site).use_rental_ticket(5001)
        assert not ok and site.posts == [], (kind, reason)
    # Scenario 4: 기다무로 열 수 있는 회차(기다무가 있는 작품의 일반 잠긴 회차)에는 쓰지 않는다 — 낭비 방지
    site = Site([item(1)])
    ok, reason, _ = await client(site).use_rental_ticket(5001)
    assert not ok and site.posts == [] and "기다무" in reason, reason
    # Scenario 5: 기다무가 없는 작품은 잠긴 회차가 곧 대여권 대상이다
    site = Site([item(1)], series_waitfree=False)
    ok, _, remaining = await client(site).use_rental_ticket(5001)
    assert ok and remaining == 2 and len(use_posts(site)) == 1
    # Scenario 6: 서버가 사용을 거절하면 실패로 돌려주고 다시 시도하지 않는다
    site = Site([item(1, blocked=True)], fail_use=[5001])
    ok, reason, _ = await client(site).use_rental_ticket(5001)
    assert not ok and len(use_posts(site)) == 1 and site.rental == 3, reason
    # Scenario 7: 응답이 유실돼도 서버가 처리했다면(대여 중이 됨) 성공으로 보고, 같은 회차에 다시 쓰지 않는다(이중 소모 방지)
    site = Site([item(1, blocked=True)], lose_response=[5001])
    ok, reason, remaining = await client(site).use_rental_ticket(5001)
    assert ok and len(use_posts(site)) == 1 and site.rental == 2, (ok, reason, site.posts)
    # Scenario 8: 응답이 유실됐고 서버도 처리 못 했다면 실패로 보고 다시 시도하지 않는다
    site = Site([item(1, blocked=True)], lose_without_effect=[5001])
    ok, reason, _ = await client(site).use_rental_ticket(5001)
    assert not ok and len(use_posts(site)) == 1 and site.rental == 3
    print("1) 대여권 사용(client) OK — RT01/기다무 불가 회차/1회 호출/응답 유실 처리")


def names(d): return sorted(p.name for p in Path(d).iterdir() if p.suffix == ".zip")


async def auto_download():
    def run(site, **k):
        k.setdefault("max_episodes", 20)
        return kp.run_download(client(site), series_id=1, title=None, download_root=str(root), **k)
    # Scenario 9: 설정이 꺼져 있으면(기본) 대여권은 쓰지 않는다 — 기존 동작 그대로
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True)])
    r = await run(site)
    assert r.downloaded == [1] and use_posts(site) == [] and r.rental_used == [], r
    # Scenario 10: 켜져 있으면 기다무로 못 여는 회차를 보유 장수(2장)까지 번호순으로 쓰고, 다음 회차에서 멈춘다 — 그 회차는 "대여권 충전 필요"
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2)
    r = await run(site, use_rental_tickets=True)
    assert r.downloaded == [1, 2, 3] and r.rental_used == [2, 3] and r.rental_remaining == 0 and r.failed is None, (r.downloaded, r.rental_used)
    assert names(root / "작품") == ["0001_1화.zip", "0002_2화.zip", "0003_3화.zip"], names(root / "작품")
    assert [p[1] for p in use_posts(site)] == [{"product_id": 5002, "ticket_type": "RT01"}, {"product_id": 5003, "ticket_type": "RT01"}]
    assert r.ticket_needed is not None and r.ticket_needed.number == 4
    # Scenario 11: 기다무 한 장과 대여권을 함께 — 기다무로 열 수 있는 첫 잠긴 회차는 기다무(RT05), 그 뒤 기다무 불가 회차는 대여권(RT01)
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2), item(3, blocked=True), item(4, blocked=True)], rental=5, waitfree=True)
    r = await run(site, use_rental_tickets=True)
    assert r.downloaded == [1, 2, 3, 4] and r.ticket_used == 2 and r.rental_used == [3, 4] and r.rental_remaining == 3, (r.downloaded, r.ticket_used, r.rental_used)
    assert [p[1]["ticket_type"] for p in use_posts(site)] == ["RT05", "RT01", "RT01"] and r.ticket_needed is None
    # Scenario 12: 앞의 잠긴 회차가 기다무 충전 대기(기다무로 열 수 있는 회차)라도 건너뛰고, 그 뒤의 기다무로 못 여는 회차에 대여권을 쓴다
    #   기다무 대기 회차는 기다무가 알아서 열 차례가 오므로 대여권을 쓰지 않는다 — 번호는 비지만 받는 회차는 늘 누락 회차 비교로 채워진다
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=5, waitfree=False)
    r = await run(site, use_rental_tickets=True)
    assert r.downloaded == [1, 3] and r.rental_used == [3] and [p[1]["product_id"] for p in use_posts(site)] == [5003], (r.downloaded, r.rental_used)
    assert r.ticket_needed is None                       # 남은 잠긴 회차(2번)는 기다무가 열 회차라 대여권이 필요한 게 아니다
    # Scenario 13: "N일 후 무료"(숨김/무료 전환 예정) 회차에는 쓰지 않는다 — 기다리면 열린다. 건너뛰고 뒤의 대여권 회차에는 쓴다
    for extra in ({"hidden": True}, {"free_dt": "2099-01-01T00:00:00+09:00"}):
        root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True, **extra)], rental=5)
        r = await run(site, use_rental_tickets=True)
        assert r.downloaded == [1] and use_posts(site) == [], extra
        root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True, **extra), item(3, blocked=True)], rental=5)
        r = await run(site, use_rental_tickets=True)
        assert r.downloaded == [1, 3] and [p[1]["product_id"] for p in use_posts(site)] == [5003], extra
    # Scenario 14: 보유 0장이면 아무것도 쓰지 않고, 기존 "대여권 충전 필요" 판정이 그대로 나온다
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True)], rental=0, ticket_type=None)
    r = await run(site, use_rental_tickets=True)
    assert r.downloaded == [1] and use_posts(site) == [] and r.ticket_needed.number == 2
    # Scenario 15: 열고 나서 이미지 받기에 실패하면 거기서 멈추고 더 쓰지 않는다 — 이미 대여 중이라 다음 실행에서 받는다(회차 이중 소모 없음)
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True)], rental=3, no_image=[5002])
    r = await run(site, use_rental_tickets=True)
    assert r.failed == 2 and r.rental_used == [2] and len(use_posts(site)) == 1 and r.ticket_needed is None
    # Scenario 16: 사용이 거절되면 거기서 멈춘다
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True)], rental=3, fail_use=[5002])
    r = await run(site, use_rental_tickets=True)
    assert r.downloaded == [1] and r.rental_used == [] and len(use_posts(site)) == 1

    # Scenario 17: 실제 사례(블루엔젤) — 앞쪽에 기다무 대기 회차(2,3번)가 있고, 예전에 대여권으로 받아 둔 회차(4,5번: 지금은 대여가 끝나 잠겨 보임)와
    #   방금 받은 회차(6,7번)가 있고, 아직 안 받은 기다무 불가 회차(8,9번)가 남은 작품. 보유 대여권 2장은 이미 받은 4,5번이 아니라 8,9번에 쓰인다
    root = Path(tempfile.mkdtemp()); folder = root / "작품"; folder.mkdir()
    for n in (1, 4, 5, 6, 7): (folder / f"{n:04d}_{n}화.zip").write_bytes(b"x")          # 이미 받아 둔 파일(번호와 제목 규칙만 맞으면 된다)
    site = Site([item(1, free=True), item(2), item(3), item(4, blocked=True), item(5, blocked=True), item(6, free=True), item(7, free=True),
                 item(8, blocked=True), item(9, blocked=True), item(10, blocked=True)], rental=2, waitfree=False)
    r = await run(site, use_rental_tickets=True)
    assert r.rental_used == [8, 9] and r.rental_remaining == 0 and [p[1]["product_id"] for p in use_posts(site)] == [5008, 5009], (r.rental_used, site.posts)
    assert sorted(p.name for p in folder.glob("*.zip")) == [f"{n:04d}_{n}화.zip" for n in (1, 4, 5, 6, 7, 8, 9)]
    assert r.ticket_needed is not None and r.ticket_needed.number == 10          # 대여권이 떨어져서 다음 대여권 회차(10번)가 "충전 필요"
    # Scenario 18: 대여권 회차가 남았는데 보유 수는 충분하지만 작품당 상한(max_episodes)에 걸려 못 연 경우는 "충전 필요"가 아니다 — 다음 실행에서 이어진다
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=5)
    r = await run(site, use_rental_tickets=True, max_episodes=3)
    assert r.downloaded == [1, 2, 3] and r.rental_used == [2, 3] and r.rental_remaining == 3 and r.ticket_needed is None
    # Scenario 19: 대여권을 쓰려고 확인한 적이 없으면(상한이 이미 차서 반복문이 안 돎) 보유 수를 모르므로 "충전 필요"라고 단정하지 않는다
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2, blocked=True)], rental=0, ticket_type=None)
    r = await run(site, use_rental_tickets=True, max_episodes=1)
    assert r.downloaded == [1] and r.rental_remaining is None and r.ticket_needed is None and use_posts(site) == []
    # Scenario 20: 설정이 꺼져 있을 때의 "다음 회차가 대여권 필요" 판정은 예전과 같다(첫 잠긴 회차만 본다 — 앞에 기다무 대기 회차가 있으면 알리지 않음)
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=0, waitfree=False)
    r = await run(site)
    assert r.downloaded == [1] and r.ticket_needed is None
    # Scenario 21: 로그 — 건너뛴 앞 회차 수, 대여권 0장으로 멈춘 이유(작품 단위로 멈춘다는 말), 사용 기록이 남는다
    lines = []
    root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2), item(3), item(4, blocked=True)], rental=0, ticket_type=None, waitfree=False)
    await run(site, use_rental_tickets=True, on_progress=lines.append)
    assert any("4번 회차" in l and "대여권이 없습니다" in l and "이 작품의 대여권 사용은 여기서 멈춥니다" in l for l in lines), lines
    assert any("기다무로 열 수 있거나" in l and "2개" in l for l in lines), lines
    lines.clear(); root = Path(tempfile.mkdtemp()); site = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=1, waitfree=False)
    await run(site, use_rental_tickets=True, on_progress=lines.append)
    assert any("3번 회차를 대여권으로 열었습니다" in l and "남은 대여권 0장" in l for l in lines), lines
    print("2) 자동 다운로드 OK — 설정 꺼짐/보유 수까지/기다무와 함께/대기 회차 건너뛰기/이미 받은 회차 제외/실패 처리/로그")


async def manual_download():
    root = tempfile.mkdtemp()
    sel = lambda site, nums, **k: kp.download_selected(client(site), series_id=1, title=None, numbers=nums, download_root=root, **k)
    # Scenario 17: 확인 값(use_rental_tickets)이 없으면 쓰지 않고 건너뛴다
    site = Site([item(1, free=True), item(2, blocked=True)])
    r = await sel(site, [1, 2])
    assert r.downloaded == [1] and r.skipped_locked == [2] and use_posts(site) == []
    # Scenario 18: 확인 값이 있으면 선택한 기다무 불가 회차에 번호순으로 보유 수(2장)까지만 쓴다 — 나머지는 "대여권 부족"으로 건너뜀
    root = tempfile.mkdtemp(); site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2)
    r = await sel(site, [4, 2, 3, 1], use_rental_tickets=True)
    assert r.downloaded == [1, 2, 3] and r.rental_used == [2, 3] and r.skipped_locked == [4] and r.rental_remaining == 0, (r.downloaded, r.rental_used, r.skipped_locked)
    assert [p[1]["product_id"] for p in use_posts(site)] == [5002, 5003]
    # Scenario 19: 기다무로 열 수 있는 회차는 확인 값이 있어도 대여권으로 열지 않는다(기다무 규칙 그대로)
    root = tempfile.mkdtemp(); site = Site([item(1, free=True), item(2)], rental=3, waitfree=False)
    r = await sel(site, [1, 2], use_rental_tickets=True)
    assert r.downloaded == [1] and r.skipped_locked == [2] and use_posts(site) == []
    print("3) 수동 다운로드 OK — 확인 값 필요/보유 수까지/기다무 가능 회차는 제외")


def no_purchase():
    # Scenario 20: 구매/결제 주소는 코드 어디에도 없다 — 허용된 요청은 읽기 + ticket/use뿐
    source = inspect.getsource(kp)
    for forbidden in ("ticket/buy", "/store/", "store/view", "inven/", "cash"):
        assert forbidden not in source.replace("cash_amount", ""), forbidden
    assert kp.RENTAL_TICKET_TYPE == "RT01" and kp.WAITFREE_TICKET_TYPE == "RT05"
    # 이 모듈만이 아니라 앱 전체(파이썬/자바스크립트)에 구매 주소가 없다
    root = Path(kp.__file__).resolve().parent
    for path in list(root.rglob("*.py")) + list((root / "static").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("ticket/buy", "store/view", "store/get", "inven/open"):
            assert forbidden not in text, (path.name, forbidden)
    print("4) 구매/결제 코드 없음 OK")


asyncio.run(client_level())
asyncio.run(auto_download())
asyncio.run(manual_download())
no_purchase()
print("ALL OK")
