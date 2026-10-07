"""네이버 수동 다운로드 — 검색 결과/분석 결과가 이 프로그램의 구독 상태(subscription)를 함께 알려 준다(카카오와 같은 방식).

구독 상태는 DB의 status(active/unsubscribed/excluded/…) 그대로이고, 아직 등록 안 한 작품은 None이다.
"""
import asyncio

import httpx

from app import db, repository, manual_download, naver_api
from app.models import SearchResultItem, TitleInfo
db.get_connection()
import app.main as m


def result(title_id, name):
    return SearchResultItem(title_id=title_id, title_name=name, thumbnail_url=f"https://t/{title_id}.jpg", is_finished=False, is_paused=False, is_adult=False, has_update=False)


async def fake_search(session, keyword, timeout_seconds):
    return [result("111", "구독중 작품"), result("222", "제외한 작품"), result("333", "처음 보는 작품")]


async def fake_analyze(title_id, settings):
    info = TitleInfo(title_id=title_id, title_name=f"작품{title_id}", synopsis="", is_adult=False, webtoon_type="webtoon", is_finished=False, thumbnail_url=f"https://t/{title_id}.jpg")
    return info, []


naver_api.search_webtoons = fake_search
manual_download.analyze = fake_analyze


async def main():
    repository.upsert_new("111", "구독중 작품"); repository.set_status("111", repository.STATUS_ACTIVE)
    repository.upsert_new("222", "제외한 작품"); repository.set_status("222", repository.STATUS_EXCLUDED)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 1: 검색 결과 카드가 작품마다 구독 상태를 알려 준다
        r = await c.get("/api/manual-download/search", params={"query": "작품"})
        assert r.status_code == 200, r.text
        assert {x["title_id"]: x["subscription"] for x in r.json()} == {"111": "active", "222": "excluded", "333": None}, r.json()
        print("1) 검색 결과의 구독 상태 OK (구독중/제외/미등록)")

        # Scenario 2: 분석 결과도 구독 상태와 썸네일(구독할 때 필요)을 알려 준다
        r = await c.get("/api/manual-download/analyze", params={"title_id": "111"})
        assert r.status_code == 200, r.text
        assert r.json()["subscription"] == "active" and r.json()["thumbnail_url"] == "https://t/111.jpg", r.json()
        r = await c.get("/api/manual-download/analyze", params={"title_id": "333"})
        assert r.json()["subscription"] is None, r.json()
        print("2) 분석 결과의 구독 상태/썸네일 OK")

        # Scenario 3: 이미 있는 구독 API로 구독하면 이후 검색 결과가 구독 중으로 바뀐다
        r = await c.post("/api/naver-list/333/subscribe", json={"title": "처음 보는 작품", "thumbnail_url": "https://t/333.jpg"})
        assert r.status_code == 200, r.text
        r = await c.get("/api/manual-download/search", params={"query": "작품"})
        assert {x["title_id"]: x["subscription"] for x in r.json()}["333"] == "active"
        print("3) 구독 후 상태 반영 OK")

asyncio.run(main())
print("ALL OK")
