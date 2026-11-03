"""네이버 작품 정보 API(/api/article/list/info)로 연재 요일 알아내기.

배경: 휴재 중인 성인(18세) 작품은 요일별 목록에서 빠지고, 로그인 없이 읽는 모바일 작품 페이지에도 요일 칸이 없어서 "기타"에 남았다(실제 사례: 소꿉친구 컴플렉스 목요, 화스트 페이스 일요 —
둘 다 휴재). 실제 응답(HAR 캡처)의 작품 정보 API에는 publishDayOfWeekList가 있다. 그래서 요일 조회는 정보 API가 먼저이고, 요일을 못 얻을 때만(완결이 아닌데 비어 있거나 요청이 실패)
예전의 모바일 작품 페이지로 한 번 더 확인한다. 정보를 받는 곳(enrich_one — 전체 재동기화/신작 스캔)은 그 요일도 같이 저장한다.
"""
import asyncio
import json
from pathlib import Path

from app import db, naver_api, repository, tracker, weekdays
from app.config import get_settings
from app.models import TitleInfo, TitlePage
db.get_connection()
from fake_naver_http import FakeResponse, FakeSession

FIX = Path(__file__).resolve().parent.parent / "fixtures"
raw = lambda tid: json.loads((FIX / f"naver_info_{tid}.json").read_text(encoding="utf-8"))
PAGE = (FIX / "naver_mobile_list_787500.html").read_text(encoding="utf-8")                       # 실제 모바일 페이지(은탄, 토요일, 완결 아님)

# Scenario 1: 실제 응답 파싱 — 요일(mon~sun), 휴재, 성인, 완결 아님. 요일 필드가 없거나 비어 있으면 빈 목록
a = naver_api._parse_title_info(raw("822640"), "822640"); b = naver_api._parse_title_info(raw("847905"), "847905")
assert (a.weekdays, a.is_paused, a.is_adult, a.is_finished, a.title_name) == (["thu"], True, True, False, "소꿉친구 컴플렉스"), a
assert (b.weekdays, b.is_paused, b.is_adult) == (["sun"], True, True), b
multi = raw("822640"); multi["publishDayOfWeekList"] = ["WEDNESDAY", "MONDAY"]; assert naver_api._parse_title_info(multi, "1").weekdays == ["mon", "wed"], "월~일 순서"
none = raw("822640"); none.pop("publishDayOfWeekList"); assert naver_api._parse_title_info(none, "1").weekdays == []
none["publishDayOfWeekList"] = None; assert naver_api._parse_title_info(none, "1").weekdays == []
print("1) 파싱 OK")

# Scenario 2: 요일 조회 — 정보 API가 요일을 주면 그것으로 끝(모바일 페이지는 요청도 안 함). 완결이면 요일이 비어도 그대로(폴백 없음)
INFO_URL, PAGE_URL = naver_api.NAVER_INFO_URL, naver_api.NAVER_MOBILE_LIST_URL
def site(info=None, page=None):
    """info/page: FakeResponse 또는 예외 또는 None(그 주소는 불리면 안 된다)."""
    def handler(url, params, attempt):
        outcome = {INFO_URL: info, PAGE_URL: page}[url]
        if outcome is None: raise AssertionError(f"불리면 안 되는 주소: {url}")
        if isinstance(outcome, BaseException): raise outcome
        return outcome
    return FakeSession(handler)
look = lambda session, tid="1": asyncio.run(naver_api.fetch_title_weekdays(session, tid, 5))
s = site(info=FakeResponse(200, raw("822640"))); assert look(s) == TitlePage(status=200, days=["thu"], finished=False, paused=True), "정보 API는 휴재(rest)도 알려 준다"
assert [c[0] for c in s.calls] == [INFO_URL] and s.calls[0][1] == {"titleId": "1"}
finished = raw("822640"); finished["finished"] = True; finished["publishDayOfWeekList"] = []
s = site(info=FakeResponse(200, finished)); assert look(s) == TitlePage(status=200, days=[], finished=True, paused=True) and [c[0] for c in s.calls] == [INFO_URL], "완결은 요일이 없어도 페이지를 더 보지 않는다(요청도 안 한다)"
print("2) 정보 API 우선 OK")

# Scenario 3: 폴백 — 정보 API가 200인데 요일이 비어 있고 완결도 아니면, 모바일 페이지로 한 번 더. 정보 API가 실패(HTTP 오류/예외/JSON 아님)해도 페이지로
empty = raw("822640"); empty["publishDayOfWeekList"] = []
s = site(info=FakeResponse(200, empty), page=FakeResponse(200, text=PAGE)); assert look(s) == TitlePage(status=200, days=["sat"], finished=False, paused=True) and [c[0] for c in s.calls] == [INFO_URL, PAGE_URL]
assert look(site(info=FakeResponse(403), page=FakeResponse(200, text=PAGE))) == TitlePage(status=200, days=["sat"], finished=False, paused=None), "정보 API가 실패하면 휴재 여부는 모른다(None)"
assert look(site(info=ConnectionError("끊김"), page=FakeResponse(200, text=PAGE))).days == ["sat"]
class BadJson(FakeResponse):
    async def json(self, *a, **k): raise ValueError("JSON 아님")
assert look(site(info=BadJson(200), page=FakeResponse(200, text=PAGE))).days == ["sat"]
print("3) 폴백 OK")

# Scenario 4: 둘 다 못 얻으면 정보 API의 상태를 남긴다(진단용): 정보 403 + 페이지 503 → 403, 둘 다 예외 → 상태 없음. 페이지가 200이지만 요일이 없으면(정보도 비어 있음) 요일 없음(200)
assert look(site(info=FakeResponse(403), page=FakeResponse(503))) == TitlePage(status=403, days=[], finished=None)
assert look(site(info=ConnectionError("a"), page=ConnectionError("b"))) == TitlePage(status=None, days=[], finished=None)
assert look(site(info=FakeResponse(200, empty), page=FakeResponse(200, text="<html>요일 없는 페이지</html>"))) == TitlePage(status=200, days=[], finished=False, paused=True), "정보 API가 말한 완결/휴재 여부를 유지"
print("4) 상태 남기기 OK")

# Scenario 5: 정보를 받는 곳(enrich_one)이 요일도 조회 결과에 저장한다 — 요일별 목록에 없는 휴재 작품의 요일을 전체 재동기화/신작 스캔이 한 번에 채운다. 완결 여부와 HTTP 200도 함께(진단용)
INFO = {}
async def fake_info(session, title_id, timeout): return INFO.get(title_id)
naver_api.fetch_title_info = fake_info
mk = lambda tid, days, finished=False: TitleInfo(title_id=tid, title_name=f"작품{tid}", synopsis="", is_adult=True, webtoon_type="webtoon", is_finished=finished, thumbnail_url="u", weekdays=days, is_paused=True)
for tid, status in (("w1", repository.STATUS_EXCLUDED), ("w2", repository.STATUS_ACTIVE), ("w3", repository.STATUS_EXCLUDED)):
    repository.upsert_new(tid, f"작품{tid}"); repository.set_status(tid, status)
INFO.update(w1=mk("w1", ["thu"]), w2=mk("w2", ["mon", "wed"]), w3=mk("w3", [], finished=True))
for tid in INFO: assert asyncio.run(tracker.enrich_one(None, tid, get_settings(), register_authors_enabled=False))[0]
saved = weekdays._get_lookup()
assert saved["w1"]["days"] == ["thu"] and saved["w1"]["ok"] and saved["w1"]["http"] == 200 and saved["w1"]["finished"] is False, saved["w1"]
assert saved["w2"]["days"] == ["mon", "wed"], "구독 중인 작품도 요일을 저장한다(목록에 있으면 목록 요일이 우선이다)"
assert saved["w3"]["days"] == [] and saved["w3"]["finished"] is True
assert weekdays.get_naver_map() == {"w1": ["thu"], "w2": ["mon", "wed"]}, weekdays.get_naver_map()
# 요일이 비어 있고 완결도 아니면 저장하지 않는다 — "요일 없음"으로 기록해 버리면 요일 조회(모바일 페이지 폴백 포함)가 일주일 동안 다시 안 묻는다
repository.upsert_new("w4", "작품w4"); repository.set_status("w4", repository.STATUS_EXCLUDED); INFO["w4"] = mk("w4", [])
assert asyncio.run(tracker.enrich_one(None, "w4", get_settings(), register_authors_enabled=False))[0] and "w4" not in weekdays._get_lookup(), "요일이 비어 있으면 저장하지 않는다"
INFO["w1"] = None; assert asyncio.run(tracker.enrich_one(None, "w1", get_settings(), register_authors_enabled=False))[0] is False and weekdays._get_lookup()["w1"]["days"] == ["thu"], "정보를 못 받으면 저장된 요일을 그대로 둔다"
print("ALL OK")
