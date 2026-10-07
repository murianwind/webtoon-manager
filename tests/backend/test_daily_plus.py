"""네이버 '매일+' 웹툰을 전체목록/구독해제/제외됨/수동 다운로드 검색에서 숨긴다.

매일+ 구분은 모바일 매일+ 페이지(m.comic.naver.com/webtoon/weekday?week=dailyPlus)의 본문 목록 링크에 붙는 `&week=dailyPlus`로 한다
(실제 캡처에서 확인 — 같은 페이지의 '이달의 신작' 캐러셀 링크에는 week가 없어서 매일+ 작품이 아니다). 하루에 한 번만 받아 DB에 담아 둔다.
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app import daily_plus, db, naver_api, repository
from app.models import NaverListItem, SearchResultItem
db.get_connection()
import app.main as m

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures" / "naver_daily_plus_sample.html").read_text(encoding="utf-8")


class Resp:
    def __init__(self, status=200, text=""): self.status, self._t = status, text
    async def text(self, errors="strict"): return self._t
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Session:
    def __init__(self, status=200, text=SAMPLE, boom=False): self.status, self.text, self.boom, self.calls = status, text, boom, []
    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if self.boom: raise RuntimeError("네트워크 오류")
        return Resp(self.status, self.text)


async def fetch_and_cache():
    # Scenario 1: 매일+ 본문 목록의 작품만 뽑는다 — 캐러셀(이달의 신작)의 링크는 매일+가 아니다
    assert daily_plus.parse_ids(SAMPLE) == {"852534", "823186", "855130"}, daily_plus.parse_ids(SAMPLE)
    assert daily_plus.parse_ids("<html></html>") == set()
    print("1) 파싱 OK")

    # Scenario 2: 받기 — 성공하면 목록, 실패/오류/빈 페이지(구조가 바뀜)면 None(기존 기록을 지우지 않는다)
    s = Session()
    assert await daily_plus.fetch_ids(s, 10) == {"852534", "823186", "855130"} and "m.comic.naver.com/webtoon/weekday?week=dailyPlus" in s.calls[0]
    assert await daily_plus.fetch_ids(Session(status=500), 10) is None
    assert await daily_plus.fetch_ids(Session(boom=True), 10) is None
    assert await daily_plus.fetch_ids(Session(text="<html>다른 페이지</html>"), 10) is None
    print("2) 받기 OK")

    # Scenario 3: 하루 안에는 다시 받지 않고, 하루가 지나면 다시 받고, 받기에 실패하면 예전 기록을 그대로 쓴다
    assert daily_plus.cached_ids() == set()
    s = Session(); assert await daily_plus.current_ids(10, s) == {"852534", "823186", "855130"} and len(s.calls) == 1
    s = Session(); await daily_plus.current_ids(10, s); assert s.calls == []
    stored = json.loads(repository.get_setting(daily_plus.CACHE_KEY)); stored["fetched_at"] = (datetime.now() - timedelta(hours=25)).isoformat()
    repository.set_setting(daily_plus.CACHE_KEY, json.dumps(stored))
    s = Session(boom=True); assert await daily_plus.current_ids(10, s) == {"852534", "823186", "855130"} and len(s.calls) == 1   # 실패 → 예전 기록 유지
    s = Session(text=SAMPLE.replace("855130", "999999")); assert "999999" in await daily_plus.current_ids(10, s)             # 성공 → 새 기록
    print("3) 하루 한 번 + 실패 시 예전 기록 OK")


async def screens():
    ids_hidden = {"852534"}
    daily_plus.cached_ids = lambda: set(ids_hidden)
    async def fresh(timeout_seconds, session=None): return set(ids_hidden)
    daily_plus.current_ids = fresh

    async def fake_list(session, timeout_seconds):
        mk = lambda i, n: NaverListItem(title_id=i, title_name=n, thumbnail_url="", weekdays=["MONDAY"])
        return [mk("111", "일반 작품"), mk("852534", "매일플러스 작품")]
    async def fake_search(session, keyword, timeout_seconds):
        mk = lambda i, n: SearchResultItem(title_id=i, title_name=n, thumbnail_url="", is_finished=False, is_paused=False, is_adult=False, has_update=False)
        return [mk("111", "일반 작품"), mk("852534", "매일플러스 작품")]
    naver_api.fetch_full_webtoon_list = fake_list
    naver_api.search_webtoons = fake_search

    # 구독 중/구독해제/제외됨/DB에만 있는 작품 중 매일+가 섞여 있다
    for tid, name, status in (("111", "일반 작품", repository.STATUS_ACTIVE), ("852534", "매일플러스 작품", repository.STATUS_ACTIVE),
                              ("852000", "해제한 매일+", repository.STATUS_UNSUBSCRIBED), ("333", "해제한 일반", repository.STATUS_UNSUBSCRIBED),
                              ("852001", "제외한 매일+", repository.STATUS_EXCLUDED), ("444", "제외한 일반", repository.STATUS_EXCLUDED)):
        repository.upsert_new(tid, name); repository.set_status(tid, status)
    ids_hidden.update({"852000", "852001", "852777"})
    repository.upsert_new("852777", "휴재 중인 매일+"); repository.set_status("852777", repository.STATUS_ACTIVE)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 4: 웹툰 전체목록 — 구독 중인 매일+와 목록에 없는(장기휴재) DB 작품까지 숨긴다
        titles = {x["title_id"] for x in (await c.get("/api/naver-list")).json()}
        assert "111" in titles and "333" in titles and not titles & {"852534", "852000", "852777"}, titles
        # Scenario 5: 구독해제/제외됨 탭(그리고 구독 중 목록)
        for status, shown, hidden in ((repository.STATUS_UNSUBSCRIBED, "333", "852000"), (repository.STATUS_EXCLUDED, "444", "852001"), (repository.STATUS_ACTIVE, "111", "852534")):
            ids = {x["title_id"] for x in (await c.get("/api/webtoons", params={"status": status})).json()}
            assert shown in ids and hidden not in ids, (status, ids)
        # Scenario 6: 수동 다운로드 검색
        found = {x["title_id"] for x in (await c.get("/api/manual-download/search", params={"query": "작품"})).json()}
        assert found == {"111"}, found
        print("4~6) 전체목록/구독해제/제외됨/수동 다운로드 검색 OK")

        # Scenario 7: 매일+ 목록을 한 번도 못 받았으면(기록 없음) 아무것도 숨기지 않는다 — 일반 작품이 사라지는 사고를 막는다
        ids_hidden.clear()
        titles = {x["title_id"] for x in (await c.get("/api/naver-list")).json()}
        assert {"111", "852534"} <= titles
        found = {x["title_id"] for x in (await c.get("/api/manual-download/search", params={"query": "작품"})).json()}
        assert found == {"111", "852534"}
        print("7) 목록이 없으면 숨기지 않음 OK")


asyncio.run(fetch_and_cache())
asyncio.run(screens())
print("ALL OK")
