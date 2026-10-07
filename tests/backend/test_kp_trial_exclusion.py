"""카카오페이지 '체험판' 회차 제외 — 동영상과 같은 방식(목록에서 빼고 뒤 회차 번호를 당긴다).

회차 목록은 분석/수동 다운로드/자동 다운로드가 모두 list_episodes 하나를 거치므로, 여기서 빠지면 세 곳 모두에서 빠진다.
"""
import asyncio
import json
from http.cookies import SimpleCookie

from app import db
db.get_connection()
from app import kakao_page_download as kp


class R:
    def __init__(self, data):
        self.status, self._d = 200, data; self.headers = {"Content-Type": "application/json"}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def item(order, title, slide="SD03"):
    return {"cursor_index": order, "item": {"product_id": 7000 + order, "title": title, "is_free": True, "order_value": order, "page_count": 2,
                                            "hidden": False, "slide_type": slide, "service_property": {}}}


class Site:
    def __init__(self, items): self.items = items
    def get(self, url, params=None, headers=None, timeout=None):
        return R({"result": {"series_item": {"id": 1, "title": "작품"}, "list": json.loads(json.dumps(self.items)), "has_next": False}})


_orig_sleep = asyncio.sleep
async def _no_sleep(x): await _orig_sleep(0)
kp.asyncio.sleep = _no_sleep


async def listing(items):
    series_item, episodes = await kp.KakaoPageClient(Site(items), {}, 10).list_episodes(1)
    return series_item, [(e.number, e.subtitle) for e in episodes]


async def main():
    # Scenario 1: 맨 앞 체험판은 빠지고 1화가 1번이 된다
    _, eps = await listing([item(1, "작품 체험판"), item(2, "작품 1화"), item(3, "작품 2화")])
    assert eps == [(1, "1화"), (2, "2화")], eps
    print("1) 맨 앞 체험판 제외 OK")

    # Scenario 2: 중간에 있는 체험판도 뒤 회차 번호를 당긴다
    _, eps = await listing([item(1, "작품 1화"), item(2, "작품 [체험판] 2화"), item(3, "작품 3화")])
    assert eps == [(1, "1화"), (2, "3화")], eps
    print("2) 중간 체험판 제외 + 번호 당김 OK")

    # Scenario 3: 동영상과 체험판이 함께 있으면 둘 다 번호를 당긴다
    series_item, eps = await listing([item(1, "작품 동영상 트레일러", slide="SD05"), item(2, "작품 체험판"), item(3, "작품 1화"), item(4, "작품 2화")])
    assert eps == [(1, "1화"), (2, "2화")], eps
    assert series_item["_excluded_video_count"] == 2   # 사이트 회차 수 비교(가져온 수 + 뺀 수)가 맞도록 체험판도 센다
    print("3) 동영상+체험판 함께 OK (제외 개수에 합산)")

    # Scenario 4: '체험'만 들어간 일반 회차나 프롤로그는 그대로 받는다
    _, eps = await listing([item(1, "작품 프롤로그"), item(2, "작품 체험 1화"), item(3, "작품 2화")])
    assert eps == [(1, "프롤로그"), (2, "체험 1화"), (3, "2화")], eps
    print("4) 일반 회차는 영향 없음 OK")

    # Scenario 5: 판정 함수 — 동영상 판정은 그대로, 체험판은 따로 판정한다
    assert kp.is_trial_item({"title": "작품 체험판", "slide_type": "SD03"}) and not kp.is_trial_item({"title": "작품 1화", "slide_type": "SD03"})
    assert kp.is_video_item({"title": "작품 동영상", "slide_type": "SD03"}) and not kp.is_video_item({"title": "작품 체험판", "slide_type": "SD03"})
    print("5) 판정 함수 OK")

asyncio.run(main())
print("ALL OK")
