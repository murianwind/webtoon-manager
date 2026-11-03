"""웹툰 목록의 요일 정보 — 전체목록/제외됨 화면이 요일별 탭으로 보여 주려면 모든 목록 응답에 같은 형식의 요일(mon~sun)이 있어야 한다.

- 네이버: 요일별 목록 API의 MONDAY~SUNDAY를 mon~sun으로 통일(월~일 순서, 모르는 값은 무시). 제외됨/구독해제 탭은 네이버를 부르지 않고 DB 기록으로 보여 주므로,
  전체목록을 불러올 때 요일을 설정 테이블에 저장해 두고(바뀔 때만 쓴다) 그 목록들이 거기서 채운다.
- 카카오: 요일 탭(1~7 = 월~일)마다 나온 작품에 그 요일을 붙이고(여러 요일 연재는 모두), 목록 캐시 형식을 올려서 요일 없는 예전 캐시는 버리고 다시 받는다.
- 요일 정보가 없는 작품(완결/장기 휴재처럼 요일별 목록에 없는 작품)은 빈 목록 — 화면에서 "기타"에 모인다.
"""
import asyncio
import json
import time

import httpx

from app import daily_plus, db, kakao_api, kakao_catalog, naver_api, repository, weekdays
from app.models import NaverListItem
db.get_connection()
import app.main as m

# Scenario 1: 요일 표기 통일 — 월~일 순서, 중복/모르는 값 무시
assert weekdays.from_naver(["SUNDAY", "MONDAY", "MONDAY", "DAILY", "xyz"]) == ["mon", "sun"] and weekdays.from_naver(["WEDNESDAY", "TUESDAY"]) == ["tue", "wed"]
assert weekdays.from_naver([]) == [] and weekdays.from_naver(None) == []
print("1) 요일 표기 통일 OK")

# ── 네이버 목록 가짜 응답 ──
LIVE = [
    NaverListItem(title_id="9001", title_name="월수 연재", thumbnail_url="u1", weekdays=["MONDAY", "WEDNESDAY"], author_summary="가"),
    NaverListItem(title_id="9002", title_name="일요 연재", thumbnail_url="u2", weekdays=["SUNDAY"], author_summary="나"),
    NaverListItem(title_id="9003", title_name="요일 없음", thumbnail_url="u3", weekdays=[], author_summary="다"),
]
async def fake_full_list(session, timeout): return list(LIVE)
async def fake_hidden(*a, **k): return set()
naver_api.fetch_full_webtoon_list = fake_full_list
daily_plus.current_ids = fake_hidden
for tid, name in (("9001", "월수 연재"), ("9002", "일요 연재"), ("9100", "장기 휴재 구독작"), ("9200", "제외됨 월수"), ("9201", "제외됨 모름")):
    repository.upsert_new(tid, name)
repository.set_status("9200", repository.STATUS_EXCLUDED); repository.set_status("9201", repository.STATUS_EXCLUDED)
repository.set_setting(weekdays.NAVER_MAP_KEY, None)
writes = []
_real_set = weekdays.repository.set_setting
weekdays.repository.set_setting = lambda key, value: (writes.append(key), _real_set(key, value))[1]

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 2: 전체목록(네이버) — 요일이 mon~sun으로 나온다. 요일별 목록에 없는(장기 휴재) 추적 작품은 [](기타). 요일 지도는 라이브 목록 기준으로 저장(요일 있는 것만)
        body = {w["title_id"]: w for w in (await c.get("/api/naver-list")).json()}
        assert body["9001"]["weekdays"] == ["mon", "wed"] and body["9002"]["weekdays"] == ["sun"] and body["9003"]["weekdays"] == [] and body["9100"]["weekdays"] == [], {k: v["weekdays"] for k, v in body.items()}
        assert weekdays.get_naver_map() == {"9001": ["mon", "wed"], "9002": ["sun"]}
        print("2) 네이버 전체목록 요일 OK")

        # Scenario 3: 같은 목록을 다시 받아도 바뀐 게 없으면 저장하지 않는다. 바뀌면(요일이 바뀐 작품) 저장하고 사라진 작품은 지도에서 빠진다
        writes.clear(); await c.get("/api/naver-list"); assert writes == [], writes
        LIVE[1] = NaverListItem(title_id="9002", title_name="일요 연재", thumbnail_url="u2", weekdays=["SATURDAY"], author_summary="나")
        LIVE.pop(0); await c.get("/api/naver-list"); assert writes == [weekdays.NAVER_MAP_KEY] and weekdays.get_naver_map() == {"9002": ["sat"]}
        LIVE.insert(0, NaverListItem(title_id="9001", title_name="월수 연재", thumbnail_url="u1", weekdays=["MONDAY", "WEDNESDAY"], author_summary="가"))
        LIVE.append(NaverListItem(title_id="9200", title_name="제외됨 월수", thumbnail_url="u4", weekdays=["MONDAY", "WEDNESDAY"], author_summary="라"))
        await c.get("/api/naver-list"); assert weekdays.get_naver_map()["9200"] == ["mon", "wed"]                                  # 제외된 작품도 지도에는 들어간다(제외됨 탭이 읽는다)
        print("3) 변경 때만 저장 OK")

        # Scenario 4: 제외됨/구독해제 탭(DB 목록) — 네이버를 부르지 않고 저장된 요일 지도로 채운다. 지도에 없으면 []
        excluded = {w["title_id"]: w for w in (await c.get("/api/webtoons", params={"status": "excluded"})).json()}
        assert excluded["9200"]["weekdays"] == ["mon", "wed"] and excluded["9201"]["weekdays"] == [], {k: v["weekdays"] for k, v in excluded.items()}
        active = {w["title_id"]: w for w in (await c.get("/api/webtoons", params={"status": "active"})).json()}
        assert active["9001"]["weekdays"] == ["mon", "wed"] and active["9100"]["weekdays"] == []
        print("4) 제외됨/구독해제 탭 요일 OK")
asyncio.run(main())

# Scenario 5: 카카오 — 요일 탭마다 나온 작품에 그 요일을 붙이고, 여러 요일 연재는 모두. 검색 결과로 만든 항목은 [](요일을 모름)
WEBTOON = kakao_api.WEBTOON_CATEGORY_UID
card = lambda sid, title: {"series_id": sid, "category_uid": WEBTOON, "title": title, "authors": "작가", "age_grade": 0}
by_tab = {1: [card(501, "월수"), card(502, "월만")], 2: [], 3: [card(501, "월수")], 4: [], 5: [], 6: [], 7: [card(503, "일만")]}
async def fake_tab(session, tab_uid, timeout): return by_tab[tab_uid], True
real_tab = kakao_api._fetch_weekday_tab; kakao_api._fetch_weekday_tab = fake_tab
items, ok = asyncio.run(kakao_api._fetch_weekday_catalog_uncached(None, 5)); kakao_api._fetch_weekday_tab = real_tab
days = {i["title_id"]: i["weekdays"] for i in items}
assert ok and days == {501: ["mon", "wed"], 502: ["mon"], 503: ["sun"]}, days
assert kakao_api._card_to_item(card(900, "검색결과"))["weekdays"] == []
print("5) 카카오 요일 탭 OK")

# Scenario 6: 목록 캐시 형식을 올렸다 — 요일이 없는 예전(v1) 캐시는 버려서 다시 받게 한다
assert kakao_catalog.FORMAT_VERSION == 2
kakao_catalog._state.update(loaded=False, items=None, fetched_at=None)
repository.set_setting(kakao_catalog.SETTING_KEY, json.dumps({"v": 1, "fetched_at": time.time(), "items": [{"title_id": 501}]}))
assert kakao_catalog.snapshot() == ([], None)
print("6) 옛 캐시 버림 OK")

# Scenario 7: 카카오 전체목록/제외됨 탭 응답에도 요일이 있다 — 목록에 없는(장기 휴재) 추적 작품은 []
item = lambda sid, name, days: {"title_id": sid, "title_name": name, "is_adult": False, "author_names": ["작가"], "has_update": False, "is_new": False, "is_paused": False, "is_finished": False, "thumbnail_url": "", "weekdays": days}
kakao_catalog._state.update(loaded=True, items=[item(501, "월수", ["mon", "wed"]), item(503, "일만", ["sun"]), item(504, "제외된 일요", ["sun"])], fetched_at=time.time())
kakao_catalog.ensure_fresh = lambda: False
repository.upsert_new_kakao_webtoon(504, "제외된 일요", status=repository.STATUS_EXCLUDED); repository.upsert_new_kakao_webtoon(505, "제외된 모름", status=repository.STATUS_EXCLUDED)
repository.upsert_new_kakao_webtoon(506, "장기휴재 구독", status=repository.STATUS_ACTIVE)
repository.upsert_new_kakao_webtoon(507, "제외된 완결", status=repository.STATUS_EXCLUDED); repository.set_kakao_finished(507, True)
async def kakao():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        listing = {w["title_id"]: w for w in (await c.get("/api/kakao-list")).json()["items"]}
        assert listing[501]["weekdays"] == ["mon", "wed"] and listing[503]["weekdays"] == ["sun"] and listing[506]["weekdays"] == [] and 504 not in listing, {k: v["weekdays"] for k, v in listing.items()}
        excluded = {w["title_id"]: w for w in (await c.get("/api/kakao-webtoons", params={"status": "excluded"})).json()}
        assert excluded[504]["weekdays"] == ["sun"] and excluded[505]["weekdays"] == []
        assert excluded[507]["is_finished"] is True and excluded[504]["is_finished"] is False                                  # 제외됨 화면이 완결을 가릴 수 있게 완결 여부도 준다
asyncio.run(kakao())
print("7) 카카오 목록 응답 요일 OK")
print("ALL OK")
