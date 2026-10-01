"""탈조클럽(실제 캡처): 작품명이 붙은 회차 제목("탈조클럽 31화")과 그런 이름의 표식 파일, 기다무가 없는 작품(is_waitfree False)의 이용권 처리."""
import asyncio, copy, json, os, tempfile
from http.cookies import SimpleCookie
from pathlib import Path
import httpx

from app import db, repository, kakao_page_download as kp, kakao_page_auth as auth
db.get_connection()
import app.main as m

FIX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "talzo_club_product_list.json"), encoding="utf-8"))
SERIES = FIX["result"]["series_item"]; FIRST = FIX["result"]["list"][0]["item"]
assert SERIES["is_waitfree"] is False and SERIES["title"] == "탈조클럽" and FIRST["title"] == "탈조클럽 1화"      # 실제 응답의 형태

def item(n, free=True):
    d = copy.deepcopy(FIRST); d.update(product_id=6000 + n, title=f"탈조클럽 {n}화", order_value=n, is_free=free, waitfree_blocked=False, service_property={})
    return {"cursor_index": n, "item": d}
def episodes(total=36, free_upto=31):
    from datetime import datetime
    return [kp._parse_episode(item(n, free=n <= free_upto)["item"], "탈조클럽", datetime.now()) for n in range(1, total + 1)]

# ── 1. 작품명이 붙은 표식 파일: 번호가 같으면 경고 없이 그 회차 이후부터 ──
assert [e.subtitle for e in episodes()][:2] == ["1화", "2화"]                                                      # 사이트 부제목은 작품명을 뺀 "31화"
def scan(names):
    root = Path(tempfile.mkdtemp()) / "탈조클럽"; root.mkdir()
    for n in names: (root / n).write_bytes(b"z")
    return kp.scan_existing_files(root)
eps = episodes()
plan = kp.plan_by_folder_rules(eps, scan(["0031_탈조클럽 31화.zip"]), "탈조클럽")
assert plan.mode == "single_marker" and plan.marker.warning is False and plan.marker.resolved_number == 31, plan.marker     # 경고 없음
rows = {r.episode.number: r for r in plan.rows}
assert rows[31].downloaded and rows[30].before_start and not rows[30].downloaded and plan.to_download == [] and [e.number for e in plan.locked] == [32, 33, 34, 35, 36]
# 작품명 없는 예전 이름("31화")도 그대로 인식
plan2 = kp.plan_by_folder_rules(eps, scan(["0031_31화.zip"]), "탈조클럽"); assert plan2.marker.warning is False and plan2.marker.resolved_number == 31
# 번호가 밀려 있어도(0030_탈조클럽 31화) 부제목(작품명 제거)으로 위치를 찾는다
plan3 = kp.plan_by_folder_rules(eps, scan(["0030_탈조클럽 31화.zip"]), "탈조클럽")
assert plan3.marker.warning is False and plan3.marker.resolved_number == 31 and plan3.marker.number == 30
# 정말 안 맞는 파일은 여전히 경고
plan4 = kp.plan_by_folder_rules(eps, scan(["0031_전혀 다른 제목.zip"]), "탈조클럽"); assert plan4.marker.warning is True
# 작품명 인자를 안 주면 예전 동작 그대로(호환)
plan5 = kp.plan_by_folder_rules(eps, scan(["0031_31화.zip"])); assert plan5.marker.warning is False
# 여러 파일(비교 모드)에서도 작품명이 붙은 이름으로 "이미 받음"을 인식(번호가 다른 경우)
plan6 = kp.plan_by_folder_rules(eps, scan(["0030_탈조클럽 29화.zip", "0031_탈조클럽 30화.zip"]), "탈조클럽")
assert {r.episode.number for r in plan6.rows if r.downloaded} == {29, 30, 31}                                    # 29화는 번호가 아니라 부제목(작품명 제거)으로 이미 받은 것으로 인식
print("1) 작품명이 붙은 표식 OK")

# ── 2. 기다무가 없는 작품: 이용권 표시/사용 안 함 ──
kp_calls = []
class R:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
def handler(url, params):
    kp_calls.append(url.rsplit("/api/", 1)[-1])
    if url.endswith("/user/get_profile"): return R(data={"result_code": 0, "profile": {"uid": 1}})
    if "product/list" in url:
        si = copy.deepcopy(SERIES)
        return R(data={"result": {"series_item": si, "list": [item(n, free=n <= 31) for n in range(1, 37)], "has_next": False}})
    if url.endswith("/ticket/my"): return R(data={"result_code": 0, "result": {"waitfree": {"charged_complete": True, "charged_at": "2026-10-01T00:00:00+09:00", "charged_period_by_minute": 1440}, "my": {"ticket_rental_count": 0, "ticket_own_count": 0}}})
    if "ready_to_use" in url: return R(data={"result_code": 0, "result": {"single": {"waitfree_block": False}, "available": {"ticket_rental_type": "RT05"}}})
    if url.endswith("/ticket/use"): return R(data={"result_code": 0, "result": {"ticket_uid": "1"}})
    if "content/about" in url: return R(data={"result_code": 0, "result": {"author_list": [{"name": "윤필", "role": "writer"}, {"name": "임정호", "role": "illustrator"}]}})
    if "viewer/data" in url: return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": 1, "secureUrl": f"https://dw-img-page.kakao.com/sdownload/resource?token=t{params['product_id']}"}]}}})
    if "sdownload" in url: return R(body=b"\xff\xd8\xffx", ctype="image/jpeg")
    raise AssertionError(url)
class Sess:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None): return handler(str(url), dict(params or {}))
    def post(self, url, data=None, headers=None, timeout=None): return handler(str(url), dict(data or {}))
import aiohttp; aiohttp.ClientSession = Sess
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"
_o = asyncio.sleep
async def _ns(x): await _o(0)
kp.asyncio.sleep = _ns

async def main():
    kroot = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(kroot))
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": 4e9} for n in auth.REQUIRED_COOKIES])))
    folder = kroot / "탈조클럽"; folder.mkdir(); (folder / "0031_탈조클럽 31화.zip").write_bytes(b"z")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 68757181})).json()
        assert r["marker"]["warning"] is False and r["marker"]["resolved_number"] == 31                                # 경고 없음(이전에는 오탐)
        assert r["tickets"]["waitfree_supported"] is False and r["tickets"]["rental_count"] == 0                        # 이 작품은 기다무가 없다
        by = {e["number"]: e for e in r["episodes"]}
        assert by[32]["state"] == "locked" and by[32]["selectable"] is False and by[31]["downloaded"] is True          # 이용권이 "사용 가능"으로 와도 잠금 그대로
    kp_calls.clear()
    client = kp.KakaoPageClient(Sess(), {}, 10)
    res = await kp.run_download(client, series_id=68757181, title=None, download_root=str(kroot), max_episodes=20)
    assert res.downloaded == [] and res.ticket_used is None and res.plan.marker.warning is False
    assert not [c for c in kp_calls if "ticket" in c], kp_calls                                                        # 기다무가 없는 작품은 이용권 API를 부르지도 않는다
    sel = await kp.download_selected(client, series_id=68757181, title=None, numbers=[32], download_root=str(kroot))
    assert sel.downloaded == [] and sel.skipped_locked == [32] and not [c for c in kp_calls if "ticket/use" in c]
    print("2) 기다무 없는 작품 OK (표시/선택/자동/수동 모두 이용권 사용 안 함)")
    # 기다무가 있는 작품(is_waitfree True)은 예전처럼 동작
    SERIES["is_waitfree"] = True; kp_calls.clear()
    res = await kp.run_download(client, series_id=68757181, title=None, download_root=str(kroot), max_episodes=20)
    assert res.ticket_used == 32 and any("ticket/use" in c for c in kp_calls)
    print("3) 기다무 있는 작품 OK")
asyncio.run(main())
print("\n전부 통과")
