"""자동 다운로드 한 번(_download_kakao_subscriptions)에서 실제로 "대여권 충전이 필요합니다" / "더 이상 받을 회차가 없습니다" 알림이 가고, 다음 실행에서는 다시 안 가는지."""
import asyncio, json, tempfile, time
from datetime import datetime
from zoneinfo import ZoneInfo
from http.cookies import SimpleCookie
from pathlib import Path
import aiohttp

from app import db, discord_config, discord_notify, repository, kakao_page_auth as auth, kakao_page_download as kp, job_status, scheduler
from app import kakao_cover
from app.config import get_settings
db.get_connection()

class R:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def text(self, errors="strict"): return ""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

HIATUS = set()  # 휴재(on_issue "P")로 내려주는 작품 번호
FINISHED = set()  # 완결(on_issue "N")로 내려주는 작품 번호 — 나머지는 연재 중("Y")
ITEMS = {}  # (5번 작품만 사이트 제목이 달라 폴더가 겹치지 않는다) series_id -> (waitfree 여부, [(번호, 부제목, 무료 여부, waitfree_blocked)])
def handler(method, url, params, data):
    if url.endswith("/user/get_profile"): return R(data={"result_code": 0, "profile": {"uid": 1}})
    if "product/list" in url:
        waitfree, spec = ITEMS[params["series_id"]]
        items = [{"cursor_index": o, "item": {"product_id": o, "title": f"작품 {sub}", "is_free": free, "order_value": o, "page_count": 2, "hidden": False, "slide_type": "SD03",
                  "waitfree_blocked": blocked, "service_property": {"purchase_info": {"purchase_type": "not_purchased"}}}} for o, sub, free, blocked in spec]
        return R(data={"result": {"series_item": {"title": "작품" if params["series_id"] != 5 else "연재작품", "on_issue": "N" if params["series_id"] in FINISHED else "P" if params["series_id"] in HIATUS else "Y", "on_sale_count": 2, "is_waitfree": waitfree}, "list": items, "has_next": False}})
    if url.endswith("/ticket/my"): return R(data={"result_code": 0, "result": {"waitfree": {"charged_complete": False, "charged_at": "2099-01-01T00:00:00+09:00"}, "my": {}}})
    if "content/about" in url: return R(data={"result_code": 0, "result": {"author_list": []}})
    if "viewer/data" in url:
        return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": i, "secureUrl": f"https://dw-img-page.kakao.com/sdownload/resource?token=t{params['product_id']}_{i}"} for i in (1, 2)]}}})
    if "sdownload" in url: return R(body=b"\xff\xd8\xffx", ctype="image/jpeg")
    raise AssertionError(url)
class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None): return handler("GET", str(url), dict(params or {}), None)
    def post(self, url, data=None, headers=None, timeout=None): return handler("POST", str(url), {}, data)
aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns; asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"

sent = []
async def fake_send(session, message):
    if not discord_config.get_webhook_url(): return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send

async def main():
    settings = get_settings(); root = Path(tempfile.mkdtemp())
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(root))
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])))
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    job_status.start("download")
    for sid in (1, 2, 3, 4, 5):
        repository.upsert_new_kakao_webtoon(sid, "작품", status=repository.STATUS_ACTIVE)
    ITEMS.update({
        1: (True, [(1, "1화", True, False), (2, "2화", False, True)]),    # 기다무 작품인데 다음(최신) 회차는 기다무로 못 엶 → 알림
        2: (False, [(1, "1화", True, False), (2, "2화", False, False)]),  # 기다무 없는 작품, 다음 회차 잠김 → 알림
        3: (True, [(1, "1화", True, False), (2, "2화", False, False)]),   # 기다무로 열 수 있는 회차(충전 대기) → 알림 없음
        4: (True, [(1, "1화", True, False), (2, "2화", True, False)]),    # 연재작을 이번에 최신 회차까지 다 받음 → "더 이상 받을 회차 없음" 알림
        5: (False, [(1, "1화", True, False), (2, "2화", False, False)]),  # 1·2번과 같지만 연재 중 → 대여권 알림 없음(완결작만 알린다)
    })
    FINISHED.update({1, 2})

    # Scenario 1: 한 번 돌리면 해당하는 작품만 알림이 간다
    await scheduler._download_kakao_subscriptions(settings, [])
    ticket_msgs = [m for m in sent if "대여권" in m]; caught_msgs = [m for m in sent if "더 이상" in m]
    assert len(sent) == 3 and len(ticket_msgs) == 2 and all(m == "[작품] 다음 회차 (2번 '2화')부터는 대여권 충전이 필요합니다." for m in ticket_msgs), sent
    assert caught_msgs == ["[작품] 더 이상 받을 회차가 없습니다. (마지막 2번 '2화')"], caught_msgs
    assert repository.get_kakao_webtoon(4)["caught_up_notified_no"] == 2
    assert repository.get_kakao_webtoon(1)["ticket_notified_no"] == 2 and repository.get_kakao_webtoon(2)["ticket_notified_no"] == 2
    assert repository.get_kakao_webtoon(3)["ticket_notified_no"] is None
    assert repository.get_kakao_webtoon(5)["ticket_notified_no"] is None, "연재 중인 작품은 대여권이 필요해도 알리지 않고 기록도 안 한다"
    print("1) 자동 다운로드에서 알림 OK (완결작 대여권 필요 2건 + 다 받음 1건, 연재 중 대여권 필요 작품은 알림 없음)")

    # Scenario 2: 다음 실행에서는 같은 회차를 다시 알리지 않는다
    await scheduler._download_kakao_subscriptions(settings, [])
    assert len(sent) == 3, sent
    print("2) 중복 알림 없음 OK")

    # Scenario 3: 연재 중이던 작품이 완결로 바뀌면 그때 알린다(기록이 없었으므로)
    FINISHED.add(5)
    await scheduler._download_kakao_subscriptions(settings, [])
    assert sent[3:] == ["[연재작품] 다음 회차 (2번 '작품 2화')부터는 대여권 충전이 필요합니다."] and repository.get_kakao_webtoon(5)["ticket_notified_no"] == 2, sent
    print("3) 완결로 바뀌면 알림 OK")

    # Scenario 4: 다운로드할 때마다 작품의 회차 수(on_sale_count)를 기록한다 — 회차가 늘지 않는 작품 알림의 재료(기록은 오늘 날짜, 아직 알리지 않은 상태)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    for sid in (3, 4):
        w = repository.get_kakao_webtoon(sid)
        assert (w["episode_count_seen"], w["episode_count_since"], w["stall_notified"]) == (2, today, False), (sid, w)
    print("4) 회차 수 기록 OK")

    # Scenario 5: 연재 중이면 같은 회차 수의 기준일을 유지하고, 휴재/완결이면 오늘로 되돌린다(연재 여부가 기록에 반영된다)
    for sid in (3, 4):
        repository.set_kakao_episode_count(sid, 2, "2026-01-01")
    HIATUS.add(4)
    await scheduler._download_kakao_subscriptions(settings, [])
    assert repository.get_kakao_webtoon(3)["episode_count_since"] == "2026-01-01", "연재 중(3번): 기준일 유지"
    assert repository.get_kakao_webtoon(4)["episode_count_since"] == today, "휴재(4번): 오늘로"
    print("5) 연재 여부 반영 OK")

asyncio.run(main())
print("ALL OK")
