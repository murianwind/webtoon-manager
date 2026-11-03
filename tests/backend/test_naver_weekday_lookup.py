"""요일별 목록에서 빠진 네이버 작품(장기 휴재 등)의 요일 채우기.

배경: 네이버의 요일별 목록은 오래 쉬는(장기 휴재) 작품을 빼서, 그런 작품은 앱에서 요일을 알 수 없어 "기타"에 남았다. 네이버 작품 페이지(모바일)에는 그 작품의 연재 요일이
있다 — 실제 HAR 캡처로 확인: `<div class="week_day">`의 연재 요일, 그리고 상태 스크립트의 weekdayTabQueryParam.
- 전체목록을 불러올 때, 요일별 목록에 없는 추적 작품(구독중/구독해제/제외됨, 완결·매일+ 제외)의 요일을 백그라운드로 작품 페이지에서 읽어 저장한다(응답을 늦추지 않는다).
- 한 번 알아낸 요일은 60일 동안 다시 묻지 않고, 못 읽었으면(요일 없음) 7일, 요청이 실패했으면 하루 뒤에 다시 시도한다. 한 번에 최대 40개.
- 요일별 목록에 있는 작품은 그 목록의 요일이 우선이다.
"""
import asyncio
import time
from pathlib import Path

import httpx

from app import daily_plus, db, naver_api, repository, weekdays
from app.models import NaverListItem, TitlePage
from fake_naver_http import FakeResponse, FakeSession
db.get_connection()
import app.main as m

FIX = Path(__file__).resolve().parent.parent / "fixtures"
page = lambda name: (FIX / name).read_text(encoding="utf-8")

# Scenario 1: 작품 페이지에서 요일 읽기 — 실제 캡처(은탄=토, 몽홀=금), 여러 요일, 요일 탭 값만 있는 경우, 요일이 없는 경우(완결 등)
assert weekdays.parse_title_page(page("naver_mobile_list_787500.html")) == ["sat"]
assert weekdays.parse_title_page(page("naver_mobile_list_644112.html")) == ["fri"]
week_day = lambda lis: f'<div class="week_day"><dt>연재</dt><dd><ul class="list_detail">{lis}</ul><ul class="property list_detail age"><li>15세 이용가</li></ul></dd></div>'
assert weekdays.parse_title_page(week_day("<li>수</li><li>월</li><li>금</li>")) == ["mon", "wed", "fri"]                       # 월~일 순서로 정리
assert weekdays.parse_title_page("<script>weekdayTabQueryParam: 'tue',</script>") == ["tue"]                                   # 연재 요일 칸이 없으면 요일 탭 값
assert weekdays.parse_title_page(week_day("<li>완결</li>")) == [] and weekdays.parse_title_page("<html></html>") == []          # 요일이 아닌 값/없음 → 빈 목록
assert weekdays.parse_title_page(week_day("<li>토</li>") + "<script>weekdayTabQueryParam: 'mon',</script>") == ["sat"]         # 둘 다 있으면 연재 요일 칸이 우선
# 완결 여부 — 상태 스크립트의 title.finished(true/false). 없으면 모름(None)
assert weekdays.parse_title_finished(page("naver_mobile_list_787500.html")) is False and weekdays.parse_title_finished(page("naver_mobile_list_644112.html")) is False
assert weekdays.parse_title_finished("<script>title: { titleId: 1, finished: true, dailyPass: false }</script>") is True and weekdays.parse_title_finished("<html></html>") is None
print("1) 작품 페이지 요일 읽기 OK")

# Scenario 2: 요청 — 모바일 작품 페이지 주소(titleId), 성공하면 요일, 요청 실패(HTTP 오류/예외)는 None(못 알아낸 것과 요일 없음을 구분)
seen = []
def handler(html, status=200):
    def h(url, params, attempt):
        seen.append((url, dict(params or {}))); return FakeResponse(status, text=html)
    return h
ok = asyncio.run(naver_api.fetch_title_page_weekdays(FakeSession(handler(page("naver_mobile_list_787500.html"))), "787500", 5))
assert ok == ["sat"] and seen[0][0] == "https://m.comic.naver.com/webtoon/list" and seen[0][1] == {"titleId": "787500"}, seen
assert asyncio.run(naver_api.fetch_title_page_weekdays(FakeSession(handler("", 503)), "1", 5)) is None
assert asyncio.run(naver_api.fetch_title_page_weekdays(FakeSession(handler("<html>요일 없음</html>")), "1", 5)) == []
class Boom:
    def get(self, *a, **k): raise ConnectionError("연결 끊김")
assert asyncio.run(naver_api.fetch_title_page_weekdays(Boom(), "1", 5)) is None
ok_page = asyncio.run(naver_api.fetch_title_page(FakeSession(handler(page("naver_mobile_list_787500.html"))), "787500", 5))
assert ok_page == TitlePage(status=200, days=["sat"], finished=False), ok_page                                                            # 상태/요일/완결을 함께 돌려준다
assert asyncio.run(naver_api.fetch_title_page(FakeSession(handler("", 403)), "1", 5)) == TitlePage(status=403, days=[], finished=None)   # HTTP 오류는 상태를 남긴다(진단용)
assert asyncio.run(naver_api.fetch_title_page(Boom(), "1", 5)) == TitlePage(status=None, days=[], finished=None)                         # 요청 자체가 실패하면 상태 없음
print("2) 작품 페이지 요청 OK")

# Scenario 3: 저장/다시 묻기 시점 — 알아낸 요일은 60일, 요일 없음은 7일, 실패는 하루 뒤에 다시. 목록(live) 요일이 조회 결과보다 우선
T0 = 1_800_000_000.0; DAY = 86400
for key in (weekdays.LOOKUP_KEY, weekdays.NAVER_MAP_KEY): repository.set_setting(key, None)
weekdays.LOOKUP_PAUSE_SECONDS = 0                                                                                                  # 테스트에서는 요청 사이를 기다리지 않는다
weekdays.save_lookup("A", ["thu"], now=T0); weekdays.save_lookup("B", [], now=T0); weekdays.save_lookup("C", None, now=T0)     # None = 요청 실패
assert weekdays.pending_lookup_ids(["A", "B", "C", "D"], now=T0 + 0.5 * DAY) == ["D"]                                              # 방금 한 것은 다시 안 물음
assert weekdays.pending_lookup_ids(["A", "B", "C", "D"], now=T0 + 1.5 * DAY) == ["C", "D"]                                          # 실패는 하루 뒤
assert weekdays.pending_lookup_ids(["A", "B", "C", "D"], now=T0 + 8 * DAY) == ["B", "C", "D"]                                       # 요일 없음은 7일 뒤
assert weekdays.pending_lookup_ids(["A", "B", "C", "D"], now=T0 + 61 * DAY) == ["A", "B", "C", "D"]                                 # 알아낸 요일도 60일 뒤엔 확인
assert weekdays.get_naver_map() == {"A": ["thu"]}                                                                                    # 조회 결과가 지도에 합쳐져 나온다(요일 없는 것은 제외)
weekdays.save_naver_map({"A": ["mon"], "E": ["sun"]}); assert weekdays.get_naver_map() == {"A": ["mon"], "E": ["sun"]}              # 목록(live) 요일이 우선, 목록이 바뀌어도 조회 결과는 남는다
weekdays.save_naver_map({"E": ["sun"]}); assert weekdays.get_naver_map() == {"A": ["thu"], "E": ["sun"]}
print("3) 저장/다시 묻기 시점 OK")

# Scenario 3b: 저장은 동시에 일어나도 서로 덮어쓰지 않는다 — 조회 결과는 한 덩어리 JSON을 읽고-바꾸고-쓰는 방식이라 보호하지 않으면 동시에 저장한 쪽이 앞의 결과를 지운다
# (운영에서는 조회가 한 번에 하나씩이라 드물지만, 이전 조회가 아직 도는 중에 다음 조회가 시작되면 생긴다 — GitHub Actions에서 실제로 걸렸다)
from concurrent.futures import ThreadPoolExecutor
repository.set_setting(weekdays.LOOKUP_KEY, None)
many = [f"c{i}" for i in range(120)]
with ThreadPoolExecutor(8) as pool: list(pool.map(lambda tid: weekdays.save_lookup(tid, ["fri"]), many))
assert all(tid in weekdays._get_lookup() for tid in many), f"동시에 저장하면 결과가 사라진다: {len(weekdays._get_lookup())}/120"
repository.set_setting(weekdays.LOOKUP_KEY, None)
print("3b) 동시 저장 OK")

# ── 네이버 목록/작품 페이지 가짜 응답 ──
LIVE = [NaverListItem(title_id="9504", title_name="목록에 있는 작품", thumbnail_url="u", weekdays=["MONDAY"], author_summary="가")]
async def fake_full_list(session, timeout): return list(LIVE)
hidden = set()
async def fake_hidden(*a, **k): return set(hidden)
naver_api.fetch_full_webtoon_list = fake_full_list; daily_plus.current_ids = fake_hidden
ANSWERS = {}; calls = []; DELAY = [0.3]                                                                                              # 처음엔 조회를 느리게 — 응답이 조회를 기다리면 바로 드러난다
FINISHED = set()                                                                                                                     # 페이지에 완결로 표시된 작품
def page_of(days, title_id): return TitlePage(status=None, days=[], finished=None) if days is None else TitlePage(status=200, days=days, finished=title_id in FINISHED)
async def fake_lookup(session, title_id, timeout): calls.append(title_id); await asyncio.sleep(DELAY[0]); return page_of(ANSWERS.get(title_id, []), title_id)
naver_api.fetch_title_weekdays = fake_lookup
def reset(ids):
    for key in (weekdays.LOOKUP_KEY, weekdays.NAVER_MAP_KEY): repository.set_setting(key, None)
    calls.clear(); ANSWERS.clear(); hidden.clear()
    for tid in ids: repository.upsert_new(tid, f"작품{tid}")
for tid, name in (("9501", "장기휴재 구독중"), ("9502", "장기휴재 제외됨"), ("9503", "완결"), ("9504", "목록에 있는 작품"), ("9505", "매일플러스")): repository.upsert_new(tid, name)
repository.set_status("9502", repository.STATUS_EXCLUDED); repository.mark_finished("9503")

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        wait = lambda: asyncio.sleep(0.9)
        # Scenario 4: 전체목록을 불러오면 응답은 바로 오고(요일을 기다리지 않는다), 백그라운드로 요일별 목록에 없는 추적 작품(완결/목록에 있는/매일+ 제외)의 요일을 알아낸다
        for key in (weekdays.LOOKUP_KEY, weekdays.NAVER_MAP_KEY): repository.set_setting(key, None)
        ANSWERS.update({"9501": ["thu"], "9502": ["sat"]}); hidden.add("9505")
        t = time.perf_counter(); first = {w["title_id"]: w for w in (await c.get("/api/naver-list")).json()}
        assert time.perf_counter() - t < 0.2 and first["9501"]["weekdays"] == [] and first["9504"]["weekdays"] == ["mon"], "첫 응답은 기다리지 않는다"
        await wait()
        assert sorted(calls) == ["9501", "9502"], calls                                                                         # 완결(9503)/목록에 있는(9504)/매일+(9505)는 묻지 않는다
        # Scenario 5: 다음에 불러오면 알아낸 요일이 나온다 — 전체목록의 장기 휴재 작품, 제외됨 탭(네이버를 부르지 않는 DB 목록)의 제외된 작품. 이미 알아낸 것은 다시 묻지 않는다
        second = {w["title_id"]: w for w in (await c.get("/api/naver-list")).json()}
        assert second["9501"]["weekdays"] == ["thu"], second["9501"]["weekdays"]
        excluded = {w["title_id"]: w for w in (await c.get("/api/webtoons", params={"status": "excluded"})).json()}
        assert excluded["9502"]["weekdays"] == ["sat"]
        await wait(); assert sorted(calls) == ["9501", "9502"], "다시 묻지 않는다: " + str(calls)
        print("4~5) 백그라운드로 채우고 다음 로드부터 반영 OK")

        # Scenario 6: 목록(요일별 목록)에 나타난 작품은 그 목록의 요일을 쓴다(조회 결과보다 우선)
        LIVE.append(NaverListItem(title_id="9501", title_name="장기휴재 구독중", thumbnail_url="u", weekdays=["TUESDAY"], author_summary="나"))
        third = {w["title_id"]: w for w in (await c.get("/api/naver-list")).json()}
        assert third["9501"]["weekdays"] == ["tue"]; LIVE.pop()
        print("6) 목록 요일 우선 OK")

        DELAY[0] = 0.01
        # Scenario 7: 요청이 실패하면(None) 하루 뒤에 다시, 한꺼번에 40개까지만, 이미 조회 중이면 또 시작하지 않는다
        for tid in range(9600, 9645): repository.upsert_new(str(tid), f"배치{tid}")
        for key in (weekdays.LOOKUP_KEY,): repository.set_setting(key, None)
        calls.clear(); ANSWERS.clear()
        await asyncio.gather(c.get("/api/naver-list"), c.get("/api/naver-list"), c.get("/api/naver-list")); await asyncio.sleep(1.2)
        batch = [x for x in calls if x.startswith("96")]
        assert len(batch) == len(set(batch)) and len(calls) == len(set(calls)) and len(calls) <= weekdays.LOOKUP_BATCH, (len(calls), len(set(calls)))   # 겹쳐 불러도 중복 조회/40개 초과 없음
        print("7) 배치 상한/중복 방지 OK")

        # Scenario 8: 조회가 실패해도(예외) 전체목록은 정상이고 다음에 다시 시도한다
        reset([])
        async def boom(session, title_id, timeout): raise RuntimeError("네이버 응답 이상")
        naver_api.fetch_title_weekdays = boom
        r = await c.get("/api/naver-list"); assert r.status_code == 200; await wait()
        naver_api.fetch_title_weekdays = fake_lookup
        r = await c.get("/api/naver-list"); assert r.status_code == 200
        print("8) 조회 실패에도 목록은 정상 OK")

        # Scenario 9: 작품 하나의 조회가 예외로 실패해도 나머지는 계속한다(실패한 건 못 알아낸 것으로 기록 → 하루 뒤 재시도).
        # (엔드포인트의 배치 순서(DB 순서)에 기대지 않고 조회 루프를 직접 불러서 결정적으로 만든다)
        from app import weekday_lookup
        if weekday_lookup._task is not None: await weekday_lookup._task                                      # 앞 시나리오의 백그라운드 조회가 끝난 뒤에 시작한다
        for key in (weekdays.LOOKUP_KEY, weekdays.NAVER_MAP_KEY): repository.set_setting(key, None)
        async def half(session, title_id, timeout):
            if title_id == "9701": raise RuntimeError("이 작품만 이상")
            return page_of(["fri"], title_id)
        naver_api.fetch_title_weekdays = half
        await weekday_lookup.run(["9701", "9702"], 5)
        saved = weekdays._get_lookup()
        assert saved["9701"]["ok"] is False and saved["9702"]["ok"] is True and saved["9702"]["days"] == ["fri"], saved
        print("9) 한 작품 실패에도 나머지 계속 OK")
        # Scenario 10: 조회 결과의 상태(HTTP)와 완결 여부도 저장한다(진단용). 페이지가 완결이라고 알려 주면 제외됨/구독해제 작품은 완결로 저장해 화면에서 가린다. 구독 중인 작품은 신작 스캔이 맡으므로 그대로
        for tid, status in (("9801", repository.STATUS_EXCLUDED), ("9802", repository.STATUS_UNSUBSCRIBED), ("9803", repository.STATUS_ACTIVE), ("9804", repository.STATUS_EXCLUDED)):
            repository.upsert_new(tid, f"작품{tid}"); repository.set_status(tid, status)
        FINISHED.update({"9801", "9802", "9803"}); ANSWERS.update({"9801": [], "9802": [], "9803": [], "9804": ["tue"]})
        await weekday_lookup.run(["9801", "9802", "9803", "9804"], 5)
        saved = weekdays._get_lookup()
        assert saved["9801"]["http"] == 200 and saved["9801"]["finished"] is True and saved["9804"]["finished"] is False, saved
        assert repository.get("9801").is_finished and repository.get("9802").is_finished, "제외됨/구독해제 완결은 저장"
        assert not repository.get("9803").is_finished and not repository.get("9804").is_finished, "구독 중이거나 연재 중이면 그대로"
        async def fails(session, title_id, timeout): return TitlePage(status=403, days=[], finished=None)
        naver_api.fetch_title_weekdays = fails
        await weekday_lookup.run(["9801"], 5)
        assert weekdays._get_lookup()["9801"]["ok"] is False and weekdays._get_lookup()["9801"]["http"] == 403, "실패는 HTTP 상태를 남긴다"
        print("10) 조회 결과의 상태/완결 저장 OK")
asyncio.run(main())
print("ALL OK")
