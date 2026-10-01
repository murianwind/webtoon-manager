"""수동 다운로드 분석의 진단 정보 — 사이트 회차 수와 가져온 회차 수 비교(회차가 빠져도 알 수 있게), 숨김/동영상 제외 수, "N일 후 무료" 날짜."""
import asyncio, copy, json, os
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import aiohttp, httpx

from app import db
db.get_connection()
import app.main as m

FIX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "talzo_club_product_list.json"), encoding="utf-8"))
SERIES = FIX["result"]["series_item"]; FIRST = FIX["result"]["list"][0]["item"]
assert SERIES["on_sale_count"] == 36 and "free_change_dt" in FIRST                                    # 실제 응답의 필드

KST = timezone(timedelta(hours=9))
def item(n, free=True, hidden=False, video=False, free_in_days=None):
    d = copy.deepcopy(FIRST); d.update(product_id=6000 + n, title=f"탈조클럽 {n}화", order_value=n, is_free=free, hidden=hidden, waitfree_blocked=False, service_property={})
    if video: d.update(slide_type="SD08", title="동영상 트레일러")
    if free_in_days is not None: d["free_change_dt"] = (datetime.now(KST) + timedelta(days=free_in_days)).isoformat()
    return {"cursor_index": n, "item": d}
STATE = {"items": []}
class R:
    def __init__(self, data=None): self.status, self._d = 200, data; self.headers = {"Content-Type": "application/json"}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return b""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Sess:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None):
        assert "product/list" in str(url), str(url)
        return R({"result": {"series_item": copy.deepcopy(SERIES), "list": STATE["items"], "has_next": False}})
aiohttp.ClientSession = Sess

async def analyze(c): r = await c.get("/api/kakao-manual/analyze", params={"series_id": 68757181}); assert r.status_code == 200, r.text; return r.json()

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 1. 사이트는 36개인데 31개만 가져온 경우(탈조클럽 스크린샷 상황) — 숫자로 드러난다 ──
        STATE["items"] = [item(n) for n in range(1, 32)]
        a = await analyze(c)
        assert (a["site_total"], a["listed_count"], a["hidden_count"], a["excluded_video_count"]) == (36, 31, 0, 0) and len(a["episodes"]) == 31, a
        print("1) 빠진 회차 진단 OK (사이트 36 / 가져온 31)")
        # ── 2. 숨김 처리된 회차는 표에서 빠지지만 몇 개인지 알려 준다 / 동영상은 따로 센다 ──
        STATE["items"] = [item(n, hidden=n in (30, 31)) for n in range(1, 32)]
        a = await analyze(c); assert a["hidden_count"] == 2 and a["listed_count"] == 31 and len(a["episodes"]) == 29, a
        STATE["items"] = [item(1, video=True)] + [item(n) for n in range(2, 37)]
        a = await analyze(c); assert a["excluded_video_count"] == 1 and a["listed_count"] == 35 and a["site_total"] == 36 and a["episodes"][0]["number"] == 1, a
        print("2) 숨김/동영상 제외 수 OK")
        # ── 3. "N일 후 무료" 회차: 잠긴 회차에 무료가 되는 날짜를 알려 준다(이미 지났거나 무료인 회차는 없음) ──
        STATE["items"] = [item(n) for n in range(1, 32)] + [item(32, free=False, free_in_days=6), item(33, free=False, free_in_days=13), item(34, free=False, free_in_days=-1)]
        a = await analyze(c); ep = {e["number"]: e for e in a["episodes"]}
        assert ep[31]["free_at"] is None and ep[32]["state"] == "locked" and ep[34]["free_at"] is None
        assert ep[32]["free_at"] == (datetime.now(KST) + timedelta(days=6)).strftime("%Y-%m-%d") and ep[33]["free_at"] == (datetime.now(KST) + timedelta(days=13)).strftime("%Y-%m-%d"), (ep[32], ep[33])
        print("3) N일 후 무료 날짜 OK")
asyncio.run(main())
print("\n전부 통과")
