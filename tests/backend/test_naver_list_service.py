"""전체목록(네이버) 만들기(naver_list_service)의 부품 — 네이버 목록 항목 + DB 구독 상태 합치기, DB에만 있는 작품 채우기(제외됨/매일+ 제외), up 값 DB 갱신.
(API를 거친 전체 흐름은 test_list_weekdays/test_naver_weekday_lookup/test_naver_list_failure가 확인한다.)
"""
import asyncio
import logging

from app import daily_plus, db, naver_api, naver_list_service as svc, repository
from app.models import NaverListItem
db.get_connection()

item = NaverListItem(title_id="1", title_name="네이버제목", thumbnail_url="https://n/1.jpg", weekdays=["TUESDAY", "THURSDAY"], author_summary="글 / 그림", is_new=True, has_update=True)

# Scenario 1: 추적 안 하는 작품 — 네이버 값 그대로, 상태 없음
entry = asyncio.run(svc._live_entry(item, None))
assert entry["title_id"] == "1" and entry["title"] == "네이버제목" and entry["weekdays"] == ["tue", "thu"] and entry["status"] is None and entry["ever_subscribed"] is False
assert entry["genres"] == [] and entry["tags"] == [] and entry["has_new_episode"] is True and entry["is_new"] is True and entry["thumbnail_url"] == "https://n/1.jpg"

# Scenario 2: 추적 중인 작품 — DB의 구독 상태/장르/태그/내 썸네일을 쓰고, 네이버의 up 값이 다르면 DB를 갱신한다(다른 탭이 최신 값을 보여 주도록). 같으면 쓰지 않는다
repository.upsert_new("1", "DB제목", False, None, repository.SOURCE_MANUAL, "https://mine/1.jpg"); repository.set_status("1", repository.STATUS_ACTIVE)
repository.update_genres_and_tags("1", ["판타지"], ["#태그"])
tracked = repository.get("1"); assert tracked.has_update is False
entry = asyncio.run(svc._live_entry(item, tracked))
assert entry["status"] == repository.STATUS_ACTIVE and entry["ever_subscribed"] is True and entry["genres"] == ["판타지"] and entry["tags"] == ["#태그"] and entry["thumbnail_url"] == "https://mine/1.jpg" and entry["title"] == "네이버제목"
assert repository.get("1").has_update is True, "up 값 갱신"
writes = []; real = repository.update_has_update; repository.update_has_update = lambda *a: (writes.append(a), real(*a))[1]
asyncio.run(svc._live_entry(item, repository.get("1"))); assert writes == [], "같은 값이면 쓰지 않는다"
repository.update_has_update = real
print("1~2) 목록 항목 OK")

# Scenario 3: DB에만 있는 작품 — 네이버 목록에 없고(장기 휴재 등) 제외됨/매일+가 아니면 보인다. 요일은 저장된 지도에서(없으면 [])
for tid, status in (("2", repository.STATUS_ACTIVE), ("3", repository.STATUS_EXCLUDED), ("4", repository.STATUS_UNSUBSCRIBED), ("5", repository.STATUS_ACTIVE)):
    repository.upsert_new(tid, f"DB{tid}"); repository.set_status(tid, status)
wt = {w.title_id: w for w in repository.list_all()}
seen, hidden = {"1"}, {"5"}
shown = [tid for tid, w in wt.items() if svc._is_db_only_shown(w, seen, hidden)]
assert sorted(shown) == ["2", "4"], shown                                                                                      # 1=목록에 있음, 3=제외됨(전용 탭), 5=매일+
only = svc._db_only_entry(wt["2"], {"2": ["fri"]}); assert only["weekdays"] == ["fri"] and only["title"] == "DB2" and only["status"] == repository.STATUS_ACTIVE
assert svc._db_only_entry(wt["4"], {})["weekdays"] == [] and set(only) == set(entry), "두 종류 항목은 같은 필드를 가진다(화면이 구분 없이 쓴다)"

# Scenario 4: 오래 걸린 날은 어디서 걸렸는지(네이버 목록 받기 / DB 읽기 / 합치기) 단계별 시간을 로그에 남긴다 — 느리다고 추측하지 않고 실제 병목을 보기 위해. 빠를 때는 로그를 안 남긴다
class Logs(logging.Handler):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, record): self.lines.append(record.getMessage())
logs = Logs(); svc.log.addHandler(logs); svc.log.setLevel(logging.INFO)
async def fake_list(session, timeout): await asyncio.sleep(0.25); return [item]
async def fake_hidden(timeout, session): return set()
naver_api.fetch_full_webtoon_list = fake_list; daily_plus.current_ids = fake_hidden
from app.config import get_settings
clock = iter([0.0, 3.5, 4.0, 4.4])      # 시계를 부르는 순서(시작 / 네이버 목록 받기 끝 / DB 읽기 끝 / 전체 끝)에 맞춘 값: 받기 3.5초, DB 0.5초, 합치기 0.4초
svc._now = lambda: next(clock)
result = asyncio.run(svc.build_naver_list(get_settings())); assert len(result) >= 1
assert any("전체목록 만들기" in l and "네이버 목록 3.5초" in l and "DB 0.5초" in l and "합치기 0.4초" in l for l in logs.lines), logs.lines
logs.lines.clear(); fast = iter([0.0, 0.1, 0.2, 0.3]); svc._now = lambda: next(fast)
asyncio.run(svc.build_naver_list(get_settings())); assert not any("전체목록 만들기" in l for l in logs.lines), "빠르면 로그 없음"
print("4) 느린 단계 로그 OK")
print("ALL OK")
