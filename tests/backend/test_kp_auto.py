"""카카오페이지 자동 다운로드(스케줄러) 테스트 — 스위치, 쿠키/로그인, 폴더 규칙, 회차 상한, 실패 격리, 이력."""
import asyncio
import json
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
import aiohttp

from app import db, repository, kakao_page_auth as auth, kakao_page_download as kp, job_status, scheduler
from app.config import get_settings
db.get_connection()

S = {"profile": "ok", "series": {}, "fail_images_for": set(), "calls": 0}
class FakeResp:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
def eps_for(spec, series_title):  # [(order, 부제목, free)] — 실제처럼 회차 제목 앞에 작품 제목이 붙는다
    return [{"cursor_index": o, "item": {"product_id": o, "title": f"{series_title} {sub}", "is_free": free, "order_value": o, "page_count": 2, "hidden": False,
             "service_property": {"purchase_info": {"purchase_type": "not_purchased"}}}} for o, sub, free in spec]
def handler(method, url, params, data):
    S["calls"] += 1
    if url.endswith("/user/get_profile"):
        return FakeResp(200, {"result_code": 0, "profile": {"uid": 1}}) if S["profile"] == "ok" else FakeResp(200, {"result_code": 401})
    if "product/list" in url:
        info = S["series"].get(params["series_id"])
        if info is None or info.get("list_fail"): return FakeResp(500)
        return FakeResp(200, {"result": {"series_item": {"title": info["title"]}, "list": eps_for(info["spec"], info["title"]), "has_next": False}})
    if "viewer/data" in url:
        files = [{"no": i, "secureUrl": f"https://page-edge.kakao.com/sdownload/resource?kid=s{params['series_id']}_p{params['product_id']}_{i}&signature=S"} for i in (1, 2)]
        return FakeResp(200, {"item": {}, "viewer_data": {"imageDownloadData": {"files": files}}})
    if "sdownload" in url:
        sid = int(url.split("kid=s")[1].split("_")[0])
        return FakeResp(500) if sid in S["fail_images_for"] else FakeResp(200, body=b"\xff\xd8\xffx", ctype="image/jpeg")
    raise AssertionError(url)
class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None):
        url = str(url); return handler("GET", url, dict(params or {}), None)
    def post(self, url, data=None, headers=None, timeout=None): return handler("POST", url, {}, data)
aiohttp.ClientSession = FakeSession
_orig = asyncio.sleep
async def _nosleep(x): await _orig(0)
kp.asyncio.sleep = _nosleep; scheduler.asyncio.sleep = _nosleep
alerts = []
async def fake_send(session, settings, message): alerts.append(message)
auth.discord_notify.send_webhook_notification = fake_send
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"
settings = get_settings()
KROOT = Path(tempfile.mkdtemp())
def export(): return json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])
def log_text(): return "\n".join(job_status.snapshot()["download"]["log"])
FREE = lambda n: [(i, f"{i}화", True) for i in range(1, n + 1)]

async def main():
    job_status.start("download")
    for sid, title in ((111, "작품A"), (222, "작품B"), (333, "작품C"), (444, "작품D"), (555, "작품E")):
        repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)
    repository.upsert_new_kakao_webtoon(999, "제외작", status=repository.STATUS_EXCLUDED)
    S["series"] = {111: {"title": "작품A", "spec": FREE(3)}, 222: {"title": "작품B", "spec": FREE(3) + [(4, "4화", False), (5, "5화", True)]},
                   333: {"title": "작품C", "spec": FREE(25)}, 444: {"title": "작품D", "spec": FREE(2)}, 555: {"title": "작품E", "list_fail": True, "spec": []}}

    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(KROOT))
    # 1. 쿠키 없음 → 건너뜀
    failures = []; await scheduler._download_kakao_subscriptions(settings, failures)
    assert S["calls"] == 0 and "쿠키가 없어서" in log_text()
    auth.save_cookies(auth.parse_cookie_export(export()))
    # 3. 로그인 풀림 → 받지 않고 알림
    S["profile"] = "out"
    await scheduler._download_kakao_subscriptions(settings, failures)
    assert not list(KROOT.rglob("*.zip")) and len(alerts) == 1 and "로그인이 풀려" in log_text()
    S["profile"] = "ok"; S["fail_images_for"] = {444}
    print("1) 쿠키/로그인 풀림 OK (쿠키 없음·풀림이면 아무것도 안 받고, 풀림은 알림)")

    # 4. 정상 실행 — 폴더 규칙 적용
    (KROOT / "작품B").mkdir(); (KROOT / "작품B" / "0002_2화#9.zip").write_bytes(b"x"); (KROOT / "작품B" / "cover.jpg").write_bytes(b"x")   # 파일 1개 → 그 이후부터
    failures = []; await scheduler._download_kakao_subscriptions(settings, failures)
    names = lambda t: sorted(p.name for p in (KROOT / t).glob("*.zip"))
    assert names("작품A") == ["0001_1화.zip", "0002_2화.zip", "0003_3화.zip"]                           # 폴더 없음 → 처음부터 전부(폴더 자동 생성)
    assert names("작품B") == ["0002_2화#9.zip", "0003_3화.zip"]                                              # 표식(2) 이후 3만 — 4는 잠겨서 멈추므로 5도 안 받음
    assert len(names("작품C")) == 20 and names("작품C")[0] == "0001_1화.zip" and names("작품C")[-1] == "0020_20화.zip"   # 작품당 20개 상한
    assert names("작품D") == [] and not list((KROOT / "작품D").glob("*.part")) if (KROOT / "작품D").exists() else True
    assert not (KROOT / "제외작").exists() and not (KROOT / "작품E").exists()                                   # 구독 중이 아닌 것/목록 실패는 폴더도 안 만듦
    assert [f["title_name"] for f in failures] == ["작품D"] and failures[0]["episode_no"] == 1                   # 실패는 격리되고 목록에 담김
    rows, total = repository.list_episode_history(status="success"); done = {(r["title_name"], r["episode_no"]) for r in rows}
    assert ("작품A", 3) in done and ("작품B", 3) in done and ("작품C", 20) in done and ("작품B", 2) not in done and total == 3 + 1 + 20
    rows, total = repository.list_episode_history(status="failed"); assert total == 1 and rows[0]["title_name"] == "작품D"
    assert "[작품A]" in log_text() and "작품E" in log_text() and "회차 목록을 가져오지 못했습니다" in log_text()
    print("2) 정상 실행 OK (폴더 없음→전부, 표식 폴더→그 이후, 20개 상한, 잠긴 회차에서 멈춤, 실패 격리, 이력 기록)")

    # 5. 다시 실행 — 남은 것만
    S["fail_images_for"] = set(); failures = []
    await scheduler._download_kakao_subscriptions(settings, failures)
    assert len(names("작품C")) == 25 and names("작품D") == ["0001_1화.zip", "0002_2화.zip"] and names("작품A")[-1] == "0003_3화.zip" and failures == []
    await scheduler._download_kakao_subscriptions(settings, failures)
    assert len(list(KROOT.rglob("*.zip"))) == 3 + 2 + 25 + 2                                                    # 더 받을 게 없으면 그대로
    print("3) 재실행 OK (남은 회차만 이어받고, 다 받았으면 새로 안 받음)")
asyncio.run(main())
print("\n전부 통과")
