"""카카오페이지 회차 목록 읽기(KakaoPageClient.list_episodes) — 페이지를 이어서 읽고, 동영상/체험판 회차는 빼고 뒤 회차 번호를 당긴다.
분석/수동 다운로드/자동 다운로드가 모두 이 함수 하나를 거친다. 일부만 읽은 목록으로 받을 회차를 정하면 안 되므로 중간에 하나라도 실패하면 None이다.
(체험판/동영상 번호 당김은 test_kp_trial_exclusion, 실제 응답 형태는 test_kp_talzo/test_kp_expiry_real_format이 따로 본다 — 여기서는 페이지 이어 읽기와 멈춤 조건을 고정한다.)
"""
import asyncio
import json
from http.cookies import SimpleCookie

from app import db
db.get_connection()
from app import kakao_page_download as kp
from app.kakao_page_download import client as kp_client


class R:
    def __init__(self, data):
        self.status, self._d = 200, data; self.headers = {"Content-Type": "application/json"}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def entry(order, title=None, slide="SD03", product_id=None):
    return {"cursor_index": order, "item": {"product_id": product_id if product_id is not None else 7000 + order, "title": title or f"작품 {order}화", "is_free": True, "order_value": order,
                                            "page_count": 2, "hidden": False, "slide_type": slide, "service_property": {}}}


class Site:
    """페이지 목록(각 페이지 = (항목들, has_next))을 순서대로 준다. fail_at 번째 요청(0부터)은 서버 오류."""
    def __init__(self, pages, fail_at=None, series_title="작품"):
        self.pages, self.fail_at, self.series_title, self.calls = pages, fail_at, series_title, []
    def get(self, url, params=None, headers=None, timeout=None):
        n = len(self.calls); self.calls.append(dict(params))
        if self.fail_at == n:
            r = R({}); r.status = 500; return r
        entries, has_next = self.pages[min(n, len(self.pages) - 1)]
        return R({"result": {"series_item": {"id": 1, "title": f"{self.series_title}{n}"}, "list": json.loads(json.dumps(entries)), "has_next": has_next}, "result_code": 0})


sleeps = []; _orig_sleep = asyncio.sleep
async def fake_sleep(x, *a, **k): sleeps.append(x); await _orig_sleep(0)
kp_client.asyncio.sleep = fake_sleep


def listing(site):
    sleeps.clear()
    return asyncio.run(kp.KakaoPageClient(site, {}, 10).list_episodes(1))


# Scenario 1: 여러 페이지를 이어서 읽는다 — 항상 첫화부터(asc), 다음 요청은 직전 페이지 마지막 항목의 cursor_index부터, 페이지 사이에 쉰다. 작품 정보는 첫 페이지 것
site = Site([([entry(1), entry(2)], True), ([entry(3), entry(4)], True), ([entry(5)], False)])
series_item, episodes = listing(site)
assert [e.number for e in episodes] == [1, 2, 3, 4, 5] and series_item["title"] == "작품0" and series_item["_excluded_video_count"] == 0, (series_item, [e.number for e in episodes])
assert len(site.calls) == 3 and [c["cursor_index"] for c in site.calls] == [0, 2, 4] and all(c["sort_type"] == "asc" and c["cursor_direction"] == "NEXT" for c in site.calls), site.calls
assert sleeps == [kp_client._PAGE_INTERVAL_SECONDS] * 2, "페이지 사이에만 쉰다: " + str(sleeps)
print("1) 페이지 이어 읽기 OK")

# Scenario 2: 멈춤 조건 — 마지막 페이지(has_next false)에서 / 새 항목이 하나도 없는 페이지(같은 페이지가 되풀이)에서 / 다음 위치(cursor)를 알 수 없을 때 / 최대 페이지 수에서
site = Site([([entry(1), entry(2)], True), ([entry(1), entry(2)], True)])                                                   # 두 번째 페이지가 같은 항목뿐 → 끝없이 읽지 않는다
_, episodes = listing(site); assert [e.number for e in episodes] == [1, 2] and len(site.calls) == 2, len(site.calls)
site = Site([([], True)]); _, episodes = listing(site); assert episodes == [] and len(site.calls) == 1, "빈 페이지에서 멈춘다"
site = Site([([entry(1)], False), ([entry(2)], False)]); _, episodes = listing(site); assert [e.number for e in episodes] == [1] and len(site.calls) == 1, "has_next가 false면 더 읽지 않는다"
noncursor = entry(2); noncursor["cursor_index"] = None; site = Site([([entry(1), noncursor], True), ([entry(3)], False)]); _, episodes = listing(site); assert len(site.calls) == 1, "다음 위치를 알 수 없으면 멈춘다"
many = [([entry(i + 1)], True) for i in range(kp_client._MAX_LIST_PAGES + 5)]; site = Site(many); _, episodes = listing(site)
assert len(site.calls) == kp_client._MAX_LIST_PAGES and len(episodes) == kp_client._MAX_LIST_PAGES, (len(site.calls), len(episodes))
print("2) 멈춤 조건 OK")

# (작품 정보의 제목이 "작품0"이라 부제목에서 작품 제목을 떼지 못한다 — 제목 떼기는 다른 테스트가 본다)
# Scenario 3: 항목 걸러내기 — 상품 번호가 없는 항목은 건너뛰고, 다른 페이지에 같은 상품이 또 나와도 한 번만 센다. 동영상/체험판은 목록에서 빼고(개수는 _excluded_video_count) 뒤 회차 번호를 당긴다 — 페이지가 달라도
no_id = entry(9); no_id["item"]["product_id"] = None
site = Site([([entry(1), no_id, entry(2, slide="SD05", title="작품 동영상")], True), ([entry(2, slide="SD05", title="작품 동영상"), entry(3), entry(3, title="중복")], True), ([entry(4, title="작품 체험판"), entry(5)], False)])
series_item, episodes = listing(site)
assert [(e.number, e.subtitle) for e in episodes] == [(1, "작품 1화"), (2, "작품 3화"), (3, "작품 5화")] and series_item["_excluded_video_count"] == 2, ([(e.number, e.subtitle) for e in episodes], series_item)
print("3) 항목 걸러내기/번호 당김 OK")

# Scenario 3b: 동영상/체험판뿐인 페이지도 "새 항목"으로 센다 — 그 페이지에서 읽기를 멈추면 뒤에 있는 진짜 회차를 놓친다(예: 맨 앞에 트레일러/체험판이 여러 개)
site = Site([([entry(1, slide="SD05", title="작품 동영상 1"), entry(2, slide="SD05", title="작품 동영상 2")], True), ([entry(3, title="작품 1화"), entry(4, title="작품 2화")], False)])
series_item, episodes = listing(site)
assert len(site.calls) == 2 and [e.number for e in episodes] == [1, 2] and series_item["_excluded_video_count"] == 2, (len(site.calls), [e.number for e in episodes])

# Scenario 4: 중간에 한 번이라도 요청이 실패하면 None — 일부만 본 목록을 돌려주지 않는다(첫 페이지 실패도 마찬가지)
assert listing(Site([([entry(1)], True), ([entry(2)], False)], fail_at=1)) is None and listing(Site([([entry(1)], False)], fail_at=0)) is None
print("4) 실패 시 None OK")

# Scenario 5: 번호가 0 이하인 항목(예고 등)은 목록에서 빠진다. 결과는 번호 오름차순
_, episodes = listing(Site([([entry(3), entry(0, title="예고"), entry(1), entry(2)], False)])); assert [e.number for e in episodes] == [1, 2, 3]
print("ALL OK")
