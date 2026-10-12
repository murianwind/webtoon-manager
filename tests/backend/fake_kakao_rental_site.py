"""카카오페이지 대여권 테스트용 가짜 서버(실제 캡처 대여권.har의 응답 형태). 여러 테스트가 같이 쓴다."""
import json
from http.cookies import SimpleCookie

JPG = b"\xff\xd8\xff\xe0jpg"


class R:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

def item(order, title=None, **k):
    d = {"product_id": 5000 + order, "title": title or f"작품 {order}화", "is_free": k.get("free", False), "order_value": order, "page_count": 2, "hidden": k.get("hidden", False),
         "slide_type": "SD03", "waitfree_blocked": k.get("blocked", False), "service_property": {}}
    if k.get("free_dt"): d["free_change_dt"] = k["free_dt"]
    return {"cursor_index": order, "item": d}


class Site:
    """가짜 카카오페이지. rental=보유 대여권 수, waitfree=기다무 충전 여부, series_waitfree=작품에 기다무가 있는지,
    ticket_type=서버가 알려 주는 사용 가능 이용권, lose_response=응답이 유실되는 회차(서버는 처리함), fail_use=사용이 거절되는 회차, no_image=이미지가 안 오는 회차."""
    def __init__(self, items, rental=3, waitfree=False, series_waitfree=True, ticket_type="RT01", lose_response=(), fail_use=(), lose_without_effect=(), no_image=()):
        self.items, self.rental, self.waitfree, self.series_waitfree, self.ticket_type = items, rental, waitfree, series_waitfree, ticket_type
        self.lose_response, self.fail_use, self.lose_without_effect, self.no_image = set(lose_response), set(fail_use), set(lose_without_effect), set(no_image)
        self.posts, self.gets, self.opened = [], [], set()
    def _item(self, pid): return next(e["item"] for e in self.items if e["item"]["product_id"] == pid)
    def get(self, url, params=None, headers=None, timeout=None):
        url = str(url); p = params or {}; self.gets.append(url.rsplit("/api/", 1)[-1])
        if "product/list" in url:
            L = []
            for e in self.items:
                e = json.loads(json.dumps(e))
                if e["item"]["product_id"] in self.opened: e["item"]["service_property"] = {"purchase_info": {"purchase_type": "rent", "rent_expire_dt": "2099-01-01 00:00:00"}}
                L.append(e)
            return R(data={"result": {"series_item": {"id": 1, "is_waitfree": self.series_waitfree, "title": "작품", "authors": "가", "on_issue": "Y", "sub_category": "액션", "age_grade": 0, "description": "d"},
                                      "list": L, "has_next": False}})
        if url.endswith("/ticket/my"):
            return R(data={"result_code": 0, "result": {"waitfree": {"charged_complete": self.waitfree, "charged_at": "2099-01-01T00:00:00+09:00", "charged_period_by_minute": 1440},
                                                       "my": {"ticket_own_count": 0, "cash_amount": 0, "ticket_rental_count": self.rental}}})
        if "ready_to_use" in url:
            pid = int(p["product_id"]); blocked = self._item(pid)["waitfree_blocked"]
            single = {"is_done": False, "waitfree_block": blocked, "read_access_type": "RAT2" if pid in self.opened else "RAT1"}
            result = {"single": single, "rental_period_by_minute": 4320, "series": {"id": 1, "is_waitfree": self.series_waitfree, "waitfree_block_count": 10}}
            if pid not in self.opened:
                result["my"] = {"ticket_own_count": 0, "cash_amount": 0, "ticket_rental_count": self.rental}
                result["purchase"] = {"ticket_own": {"ticket_id": "TKT01", "ticket_type": "TT01", "price": 500}}
                # 기다무가 충전돼 있고 이 회차를 기다무로 열 수 있으면 서버는 기다무(RT05)를 가능한 이용권으로 알려 준다(기존 코드가 믿는 동작)
                kind = "RT05" if (self.waitfree and self.series_waitfree and not blocked) else self.ticket_type
                if kind: result["available"] = {"ticket_rental_type": kind}
            return R(data={"result_code": 0, "result": result})
        if "viewer/data" in url:
            pid = int(p["product_id"]); e = self._item(pid)
            if pid in self.no_image or not (e["is_free"] or pid in self.opened): return R(data={"result_code": 1})
            return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": i, "secureUrl": f"https://img/{pid}/{i}"} for i in (1, 2)]}}})
        if "content/about" in url: return R(data={"result_code": 0, "result": {"author_list": [], "theme_keyword_list": []}})
        return R(body=JPG, ctype="image/jpeg")
    def post(self, url, data=None, headers=None, timeout=None):
        data = dict(data or {}); self.posts.append((url.rsplit("/api/", 1)[-1], data))
        pid = int(data.get("product_id", 0))
        if pid in self.lose_without_effect: raise RuntimeError("네트워크 끊김(서버는 처리 못 함)")
        if pid in self.fail_use: return R(data={"result_code": 9, "message": "사용 불가"})
        if data.get("ticket_type") == "RT01": self.rental -= 1
        else: self.waitfree = False
        self.opened.add(pid)
        if pid in self.lose_response: raise RuntimeError("네트워크 끊김(서버는 처리함)")
        return R(data={"result_code": 0, "result": {"ticket_unit_price": 300, "ticket_uid": "1", "rent_expire_dt": "2099-01-01T00:00:00+09:00", "series_id": 1}})


SITE = {}  # {"site": Site} — FakeSession이 요청을 여기 있는 가짜 서버로 넘긴다(aiohttp.ClientSession을 바꿔 끼워 쓴다)


class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None):
        if str(url).endswith("/user/get_profile"): return _Profile()
        return SITE["site"].get(url, params, headers, timeout)
    def post(self, url, data=None, headers=None, timeout=None):
        if str(url).endswith("/user/get_profile"): return _Profile()
        return SITE["site"].post(url, data, headers, timeout)


class _Profile:
    status = 200; headers = {"Content-Type": "application/json"}
    cookies = SimpleCookie()
    async def json(self, content_type=None): return {"result_code": 0, "profile": {"uid": 1}}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False



