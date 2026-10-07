"""자동 다운로드 한 번(_download_kakao_subscriptions)에서 실제로 "대여권 충전이 필요합니다" 알림이 가고, 다음 실행에서는 다시 안 가는지."""
import asyncio, json, tempfile, time
from http.cookies import SimpleCookie
from pathlib import Path
import aiohttp

from app import db, discord_config, discord_notify, repository, kakao_page_auth as auth, kakao_page_download as kp, job_status, scheduler
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

ITEMS = {}  # series_id -> (waitfree 여부, [(번호, 부제목, 무료 여부, waitfree_blocked)])
def handler(method, url, params, data):
    if url.endswith("/user/get_profile"): return R(data={"result_code": 0, "profile": {"uid": 1}})
    if "product/list" in url:
        waitfree, spec = ITEMS[params["series_id"]]
        items = [{"cursor_index": o, "item": {"product_id": o, "title": f"작품 {sub}", "is_free": free, "order_value": o, "page_count": 2, "hidden": False, "slide_type": "SD03",
                  "waitfree_blocked": blocked, "service_property": {"purchase_info": {"purchase_type": "not_purchased"}}}} for o, sub, free, blocked in spec]
        return R(data={"result": {"series_item": {"title": "작품", "on_issue": "Y", "is_waitfree": waitfree}, "list": items, "has_next": False}})
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
kp.asyncio.sleep = _ns; scheduler.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"

sent = []
async def fake_send(session, settings, message):
    if not discord_config.get_webhook_url(): return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send

async def main():
    settings = get_settings(); root = Path(tempfile.mkdtemp())
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(root))
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])))
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    job_status.start("download")
    for sid in (1, 2, 3):
        repository.upsert_new_kakao_webtoon(sid, "작품", status=repository.STATUS_ACTIVE)
    ITEMS.update({
        1: (True, [(1, "1화", True, False), (2, "2화", False, True)]),    # 기다무 작품인데 다음(최신) 회차는 기다무로 못 엶 → 알림
        2: (False, [(1, "1화", True, False), (2, "2화", False, False)]),  # 기다무 없는 작품, 다음 회차 잠김 → 알림
        3: (True, [(1, "1화", True, False), (2, "2화", False, False)]),   # 기다무로 열 수 있는 회차(충전 대기) → 알림 없음
    })

    # Scenario 1: 한 번 돌리면 해당하는 작품만 알림이 간다
    await scheduler._download_kakao_subscriptions(settings, [])
    assert len(sent) == 2 and all(m == "[작품] 다음 회차 (2번 '2화')부터는 대여권 충전이 필요합니다." for m in sent), sent
    assert repository.get_kakao_webtoon(1)["ticket_notified_no"] == 2 and repository.get_kakao_webtoon(2)["ticket_notified_no"] == 2
    assert repository.get_kakao_webtoon(3)["ticket_notified_no"] is None
    print("1) 자동 다운로드에서 알림 OK")

    # Scenario 2: 다음 실행에서는 같은 회차를 다시 알리지 않는다
    await scheduler._download_kakao_subscriptions(settings, [])
    assert len(sent) == 2, sent
    print("2) 중복 알림 없음 OK")

asyncio.run(main())
print("ALL OK")
