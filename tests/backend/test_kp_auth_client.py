"""쿠키 처리(_karmt 선택 포함)와 카카오페이지 클라이언트 테스트."""
import asyncio
import json
import time
from http.cookies import SimpleCookie

from app import repository, db
db.get_connection()
from app import kakao_page_auth as auth
from app import kakao_page_download as kp

# ── 공용 가짜 HTTP ────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json", set_cookies=None):
        self.status, self._data, self._body = status, data, body
        self.headers = {"Content-Type": ctype}
        self.cookies = SimpleCookie()
        for k, v in (set_cookies or {}).items(): self.cookies[k] = v
    async def json(self, content_type=None): return self._data
    async def read(self): return self._body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class FakeSession:
    def __init__(self, handler): self.handler, self.calls = handler, []
    def get(self, url, params=None, headers=None, timeout=None):
        url = str(url)
        self.calls.append(("GET", url, dict(params or {}), dict(headers or {}))); return self.handler("GET", url, dict(params or {}), None)
    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, dict(data or {}), dict(headers or {}))); return self.handler("POST", url, {}, dict(data or {}))

async def _nosleep(x): return None
kp.asyncio.sleep = _nosleep  # 재시도 대기 생략

REQ = auth.REQUIRED_COOKIES
def cookie_export(names=REQ, expires=None, extra=()):
    exp = expires if expires is not None else time.time() + 20 * 86400
    items = [{"domain": ".kakao.com", "name": n, "value": f"secret-{n}", "path": "/", "expirationDate": exp, "secure": True} for n in names]
    items += [{"domain": ".kakao.com", "name": n, "value": "x", "path": "/", "session": True} for n in extra]
    return json.dumps(items)

# ════════ 1. 쿠키 가져오기(Cookie-Editor JSON) ════════
c = auth.parse_cookie_export(cookie_export())
assert {x["name"] for x in c} >= set(REQ)
for bad, expect in [("", "비어"), ("not json", "JSON"), ('{"a":1}', "목록"), ("[]", "비어")]:
    try: auth.parse_cookie_export(bad); raise AssertionError(bad)
    except ValueError as e: assert expect in str(e), (bad, str(e))
try: auth.parse_cookie_export(cookie_export(names=[n for n in REQ if n not in ("_kau", "_kawlt")]))
except ValueError as e: assert "_kau" in str(e) and "_kawlt" in str(e) and "secret" not in str(e)
else: raise AssertionError("필수 쿠키 누락을 못 잡음")
other = json.loads(cookie_export()); other.append({"domain": ".example.com", "name": "junk", "value": "z", "path": "/"})
assert "junk" not in {x["name"] for x in auth.parse_cookie_export(json.dumps(other))}   # 카카오 도메인만 보관
print("1) 쿠키 JSON 검증 OK")

# ════════ 2. 저장(암호화)/상태/만료 계산 ════════
assert auth.load_cookies() is None and auth.cookie_status(None)["saved"] is False
auth.save_cookies(c)
raw = repository.get_setting(auth.SETTING_KEY)
assert raw and "secret-_kau" not in raw                              # 평문으로 저장 안 됨
assert auth.load_cookies() == c
st = auth.cookie_status(auth.load_cookies())
assert st["saved"] and st["missing"] == [] and 19 <= st["days_left"] <= 20 and st["expires_at"]
assert "secret" not in json.dumps(st)                                # 상태에 값이 새어나가지 않음
soon = auth.parse_cookie_export(cookie_export(expires=time.time() + 2 * 86400))
assert auth.cookie_status(soon)["days_left"] == 1
expired = auth.parse_cookie_export(cookie_export(expires=time.time() - 3600))
assert auth.cookie_status(expired)["days_left"] < 0
mixed = json.loads(cookie_export()); mixed[0]["expirationDate"] = time.time() + 3 * 86400                # 가장 빨리 만료되는 필수 쿠키 기준
assert auth.cookie_status(auth.parse_cookie_export(json.dumps(mixed)))["days_left"] == 2
assert auth.cookie_map(c)["_kau"] == "secret-_kau"
auth.delete_cookies(); assert auth.load_cookies() is None
print("2) 저장/상태/만료 계산 OK (암호화, 값 비노출)")

# ════════ 3. 만료 알림 문구 + 하루 1번 제한 ════════
assert auth.health_alert_message(True, 20) is None and auth.health_alert_message(None, 20) is None     # 정상/모름 → 알림 없음
assert "만료" in auth.health_alert_message(False, 10) and "다시" in auth.health_alert_message(False, 10)
assert "5" in (auth.health_alert_message(True, 4) or "") or "4" in (auth.health_alert_message(True, 4) or "")
assert auth.health_alert_message(True, 6) is None
sent = []
async def fake_send(session, settings, message): sent.append(message)
auth.discord_notify.send_webhook_notification = fake_send
async def t3():
    assert await auth.notify_if_needed(None, None, False, 10) is True and len(sent) == 1
    assert await auth.notify_if_needed(None, None, False, 10) is False and len(sent) == 1         # 24시간 안엔 또 안 보냄
    await auth.notify_if_needed(None, None, True, 30)                                              # 다시 정상이면 기록 초기화
    assert await auth.notify_if_needed(None, None, False, 10) is True and len(sent) == 2           # 그 뒤 또 만료되면 다시 알림
    assert await auth.notify_if_needed(None, None, True, 3) is True and len(sent) == 3             # 곧 만료 경고는 별도로
    assert await auth.notify_if_needed(None, None, True, 3) is False
asyncio.run(t3())
print("3) 만료 알림 OK (하루 1번, 정상 복귀 시 초기화, 곧 만료 경고 별도)")

# ════════ 4. 클라이언트: 로그인 확인 / 쿠키 헤더 / 쿠키 갱신 ════════
COOKIES = {"_kau": "A", "_kpwtkn": "B"}
def prof(ok=True): return FakeResp(200, {"result_code": 0, "profile": {"uid": 1}} if ok else {"result_code": 401, "message": "로그인 필요"})
async def t4():
    s = FakeSession(lambda m, u, p, d: prof(True))
    cl = kp.KakaoPageClient(s, dict(COOKIES), 10)
    assert await cl.check_login() is True
    m, url, _, hdr = s.calls[0]
    assert m == "POST" and url.endswith("/api/v1/user/get_profile") and "_kau=A" in hdr["Cookie"] and "_kpwtkn=B" in hdr["Cookie"]
    assert hdr["Referer"] == "https://page.kakao.com/" and "sec-ch-ua" in hdr
    assert await kp.KakaoPageClient(FakeSession(lambda *a: prof(False)), dict(COOKIES), 10).check_login() is False
    assert await kp.KakaoPageClient(FakeSession(lambda *a: FakeResp(401)), dict(COOKIES), 10).check_login() is False
    for status in (403, 429, 500):                                      # 차단/서버 문제는 "로그아웃"으로 단정하지 않는다
        assert await kp.KakaoPageClient(FakeSession(lambda *a, s=status: FakeResp(s)), dict(COOKIES), 10).check_login() is None
    def boom(*a): raise RuntimeError("network")
    assert await kp.KakaoPageClient(FakeSession(boom), dict(COOKIES), 10).check_login() is None
    # 서버가 쿠키를 새로 내려주면 반영하고 저장 대상으로 표시
    s = FakeSession(lambda *a: FakeResp(200, {"result_code": 0, "profile": {"uid": 1}}, set_cookies={"_kpwtkn": "NEW", "_extra": "E"}))
    cl = kp.KakaoPageClient(s, dict(COOKIES), 10); assert cl.cookies_changed is False
    await cl.check_login()
    assert cl.cookies["_kpwtkn"] == "NEW" and cl.cookies["_extra"] == "E" and cl.cookies_changed is True
print("4) 클라이언트 로그인 확인 OK")
asyncio.run(t4())


# ════════ 1-2. _karmt는 선택 쿠키 ════════
assert "_karmt" not in auth.REQUIRED_COOKIES and "_karmt" in auth.OPTIONAL_COOKIES and len(auth.REQUIRED_COOKIES) == 6
no_karmt = auth.parse_cookie_export(cookie_export())                                      # 필수 6개만 있어도 통과
assert "_karmt" not in {x["name"] for x in no_karmt} and auth.cookie_status(no_karmt)["missing"] == [] and auth.cookie_status(no_karmt)["optional_present"] == []
with_karmt = auth.parse_cookie_export(cookie_export(extra=("_karmt",)))                    # 있으면 알아본다
assert "_karmt" in {x["name"] for x in with_karmt} and auth.cookie_status(with_karmt)["optional_present"] == ["_karmt"]
assert auth.cookie_map(with_karmt)["_karmt"] == "x"                                        # 요청에 같이 보내지도록 값이 남는다
items = json.loads(cookie_export(extra=("_karmt",)))                                       # 선택 쿠키가 먼저 만료돼도 만료 계산엔 안 쓴다
for it in items:
    if it["name"] == "_karmt": it["expirationDate"] = time.time() + 1 * 86400
assert auth.cookie_status(auth.parse_cookie_export(json.dumps(items)))["days_left"] >= 19
try: auth.parse_cookie_export(cookie_export(names=[n for n in REQ if n != "_kau"], extra=("_karmt",)))
except ValueError as e: assert "_kau" in str(e) and "_karmt" not in str(e)
else: raise AssertionError("필수 쿠키 누락을 못 잡음")
print("1-2) _karmt 선택 처리 OK (없어도 통과, 있으면 인식, 만료 계산엔 미포함)")

# ════════ 5. 회차 목록(여러 페이지) — 번호는 사이트 회차 순서(order_value) ════════
SERIES = "닥터 최태수"
def ep(order, title, free=True, purchase="not_purchased", expire=None, hidden=False):
    pi = {"purchase_type": purchase}
    if expire: pi["rent_expire_dt"] = expire
    return {"cursor_index": order, "item": {"product_id": 1000 + order, "title": title, "is_free": free, "order_value": order, "page_count": 3,
            "hidden": hidden, "slide_type": "SD03", "service_property": {"purchase_info": pi}}}
PAGES = {
    0: [ep(1, "프롤로그"), ep(2, f"{SERIES} 1화"), ep(3, f"{SERIES} 2화"), ep(4, f"{SERIES} 3화"), ep(5, f"{SERIES} 4화")],
    5: [ep(6, f"{SERIES} 5화"), ep(7, f"{SERIES} 6화", free=False), ep(8, f"{SERIES} 7화", free=False, purchase="rent", expire="2099-01-01 00:00:00")],
    8: [ep(9, f"{SERIES} 8화", free=False, purchase="rent", expire="2000-01-01 00:00:00"), ep(10, f"{SERIES} 9화", hidden=True), ep(11, f"{SERIES} 외전"), ep(0, "번호 없음")],
}
def list_handler(m, u, p, d):
    key = p["cursor_index"]   # 첫 요청도 NEXT + asc + 순번 0(계정에 저장된 정렬에 기대는 INIT은 쓰지 않는다)
    return FakeResp(200, {"result": {"series_item": {"title": SERIES, "thumbnail": "KID/a/b"}, "list": PAGES.get(key, []), "has_next": key != 8}})
async def t5():
    s = FakeSession(list_handler); cl = kp.KakaoPageClient(s, dict(COOKIES), 10)
    series, eps = await cl.list_episodes(1234)
    assert series["title"] == SERIES and series["thumbnail"] == "KID/a/b"
    assert [e.number for e in eps] == list(range(1, 12))                                   # 번호 = order_value(프롤로그가 1번), order_value 0인 항목은 제외
    by = {e.number: e for e in eps}
    assert by[1].subtitle == "프롤로그" and by[3].subtitle == "2화" and by[11].subtitle == "외전" and by[3].product_id == 1003
    assert by[2].accessible and by[7].accessible is False and by[8].accessible is True and by[9].accessible is False       # 무료 / 잠김 / 대여 중 / 대여 만료
    assert by[8].rent_expire == "2099-01-01 00:00:00" and by[7].rent_expire is None and by[2].rent_expire is None
    assert by[10].hidden is True
    kinds = [(c[2]["cursor_direction"], c[2]["cursor_index"]) for c in s.calls]
    assert kinds == [("NEXT", 0), ("NEXT", 5), ("NEXT", 8)], kinds                          # 첫화부터, 마지막 항목의 cursor_index로 이어서 요청
    assert all(c[2]["sort_type"] == "asc" and c[2]["window_size"] == 25 for c in s.calls) and s.calls[0][2]["series_id"] == 1234
    loop = FakeSession(lambda m, u, p, d: FakeResp(200, {"result": {"series_item": {}, "list": [ep(1, "a")], "has_next": True}}))
    _, eps2 = await kp.KakaoPageClient(loop, dict(COOKIES), 10).list_episodes(1)
    assert len(eps2) == 1 and len(loop.calls) <= 3                                          # 같은 페이지만 되풀이해도 무한 반복 안 함
    assert await kp.KakaoPageClient(FakeSession(lambda *a: FakeResp(500)), dict(COOKIES), 10).list_episodes(1) is None
    print("5) 회차 목록 OK (페이지 이어받기, 번호=사이트 순서, 무료·잠김·대여 판정, 무한반복 방지)")
asyncio.run(t5())

# ════════ 6. 이미지 목록 / 이미지 받기 ════════
async def t6():
    files = [{"no": 3, "secureUrl": "https://x/3"}, {"no": 1, "secureUrl": "https://x/1"}, {"no": 2, "secureUrl": "https://x/2"}]
    cl = kp.KakaoPageClient(FakeSession(lambda *a: FakeResp(200, {"item": {}, "viewer_data": {"imageDownloadData": {"files": files}}})), dict(COOKIES), 10)
    assert await cl.viewer_image_urls(1, 2) == ["https://x/1", "https://x/2", "https://x/3"]
    assert await kp.KakaoPageClient(FakeSession(lambda *a: FakeResp(200, {"item": {}})), dict(COOKIES), 10).viewer_image_urls(1, 2) is None
    s = FakeSession(lambda m, u, p, d: FakeResp(200, body=b"IMG", ctype="image/png"))
    got = await kp.KakaoPageClient(s, dict(COOKIES), 10).download_image("https://page-edge.kakao.com/sdownload/resource?kid=k&signature=S")
    assert got == (b"IMG", "image/png") and s.calls[0][3]["Referer"] == "https://page.kakao.com/"
    assert await kp.KakaoPageClient(FakeSession(lambda *a: FakeResp(500)), dict(COOKIES), 10).download_image("https://x") is None
    print("6) 이미지 목록/받기 OK (번호순, 실패 시 재시도 후 None)")
asyncio.run(t6())
print("\n전부 통과")
