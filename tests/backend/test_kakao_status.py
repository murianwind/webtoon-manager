"""카카오 제외됨/구독해제 작품의 연재 상태(완결/휴재/서비스 종료) 확인.

배경: 카카오 요일 목록은 연재 중인 작품만 담고, 완결 여부(is_finished)는 "다운로드를 끝냈을 때"만 저장돼서, 받지 않는 제외됨/구독해제 작품은 완결이나 서비스가 끝난 작품도 그대로 보였다.
규칙: 구독 중이 아닌 카카오 작품은 작품 기본 정보(on_issue: Y 연재, P 휴재, N 완결)로 상태를 알아내 저장한다. 작품 정보가 없다고(HTTP 404/410) 하면 서비스가 끝난 것으로 본다.
일시 오류(네트워크/5xx)는 상태를 바꾸지 않고 하루 뒤에 다시. 한 번 알아낸 상태는 완결/종료 30일, 연재/휴재 7일 동안 다시 묻지 않는다. 구독 중인 작품은 이 확인이 건드리지 않는다.
"""
import asyncio

import httpx

from app import db, kakao_page_download as kp, kakao_status, repository, tracker
from app.config import get_settings
db.get_connection()
import app.main as m
from fakes import AsyncResponse
kakao_status.LOOKUP_PAUSE_SECONDS = 0                                                                                               # 테스트에서는 작품 사이를 기다리지 않는다

# Scenario 1: 클라이언트 — 작품 기본 정보 요청에서 (HTTP 상태, 작품 정보)를 돌려준다. 정보가 없으면 (상태, None), 요청 자체가 실패하면 (None, None)
class Session:
    def __init__(self, outcome): self.outcome, self.calls = outcome, []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        if isinstance(self.outcome, BaseException): raise self.outcome
        return self.outcome
fetch = lambda outcome: asyncio.run(kp.KakaoPageClient(Session(outcome), {}, 5).fetch_series_status(1))
assert fetch(AsyncResponse(200, {"result": {"series_item": {"on_issue": "N", "title": "완결작"}}})) == (200, {"on_issue": "N", "title": "완결작"})
assert fetch(AsyncResponse(200, {"result": {}})) == (200, None) and fetch(AsyncResponse(404)) == (404, None) and fetch(AsyncResponse(503)) == (503, None)
assert fetch(ConnectionError("끊김")) == (None, None)
print("1) 클라이언트 OK")

# Scenario 2: 저장/다시 묻기 시점 — 완결/종료는 30일, 연재/휴재는 7일, 일시 오류는 하루. 완결·종료만 "화면에서 가릴 대상"
T0 = 1_800_000_000.0; DAY = 86400
repository.set_setting(kakao_status.LOOKUP_KEY, None)
kakao_status.save(1, on_issue="N", http=200, now=T0); kakao_status.save(2, on_issue="Y", http=200, now=T0); kakao_status.save(3, on_issue="P", http=200, now=T0)
kakao_status.save(4, on_issue=None, http=404, now=T0); kakao_status.save(5, on_issue=None, http=503, now=T0); kakao_status.save(6, on_issue=None, http=None, now=T0)
assert kakao_status.hidden_ids() == {1, 4}, "완결(N)과 서비스 종료(404)만 가린다"
assert kakao_status.pending_ids([1, 2, 3, 4, 5, 6, 7], now=T0 + 0.5 * DAY) == [7], "방금 한 것은 다시 안 묻는다"
assert kakao_status.pending_ids([1, 2, 3, 4, 5, 6, 7], now=T0 + 1.5 * DAY) == [5, 6, 7], "일시 오류는 하루 뒤"
assert kakao_status.pending_ids([1, 2, 3, 4, 5, 6, 7], now=T0 + 8 * DAY) == [2, 3, 5, 6, 7], "연재/휴재는 7일 뒤"
assert kakao_status.pending_ids([1, 2, 3, 4, 5, 6, 7], now=T0 + 31 * DAY) == [1, 2, 3, 4, 5, 6, 7], "완결/종료도 30일 뒤엔 다시"
print("2) 저장/시점 OK")

# Scenario 3: 갱신 — 구독 중이 아닌 카카오 작품만 묻는다. 완결(N)은 DB에 완결로 저장, 다시 연재(Y)면 해제, 404는 종료로 기록(DB 완결은 건드리지 않음), 일시 오류는 상태 유지, 구독 중은 안 묻는다. 한 번에 최대 40개
repository.set_setting(kakao_status.LOOKUP_KEY, None)
for tid, name, status in ((11, "제외-완결", repository.STATUS_EXCLUDED), (12, "제외-연재", repository.STATUS_EXCLUDED), (13, "구독해제-종료", repository.STATUS_UNSUBSCRIBED), (14, "제외-오류", repository.STATUS_EXCLUDED), (15, "구독중", repository.STATUS_ACTIVE), (16, "제외-복귀", repository.STATUS_EXCLUDED)):
    repository.upsert_new_kakao_webtoon(tid, name, status=repository.STATUS_ACTIVE); repository.set_kakao_webtoon_status(tid, status)          # 구독했다가 바꾼 것으로(구독해제 목록은 구독한 적 있는 작품만 보여 준다)
repository.set_kakao_finished(16, True)                                                                                             # 예전에 완결로 저장됐지만 지금은 다시 연재 중
ANSWERS = {11: (200, {"on_issue": "N"}), 12: (200, {"on_issue": "Y"}), 13: (404, None), 14: (503, None), 15: (200, {"on_issue": "N"}), 16: (200, {"on_issue": "Y"})}
asked = []
async def fake_status(self, series_id): asked.append(series_id); return ANSWERS[series_id]
kp.KakaoPageClient.fetch_series_status = fake_status
class FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
kp.new_session = lambda: FakeSession()
refreshed = asyncio.run(tracker.refresh_inactive_kakao_status(get_settings())); assert sorted(asked) == [11, 12, 13, 14, 16] and refreshed == 4, (asked, refreshed)    # 구독 중(15)은 안 묻고, 오류(14)는 센 것에서 뺀다
finished = lambda tid: bool(repository.get_kakao_webtoon(tid)["is_finished"])
assert finished(11) is True and finished(12) is False and finished(16) is False and finished(15) is False and finished(13) is False
assert kakao_status.hidden_ids() == {11, 13}, kakao_status.hidden_ids()
saved = kakao_status.get_lookup(); assert saved["14"]["http"] == 503 and saved["14"]["on_issue"] is None and saved["12"]["on_issue"] == "Y" and saved["13"]["http"] == 404
asked.clear(); asyncio.run(tracker.refresh_inactive_kakao_status(get_settings())); assert asked == [14] or asked == [], f"방금 알아낸 것은 다시 안 묻는다: {asked}"
print("3) 갱신 OK")
for i in range(100, 150): repository.upsert_new_kakao_webtoon(i, f"많음{i}", status=repository.STATUS_EXCLUDED); ANSWERS[i] = (200, {"on_issue": "Y"})
asked.clear(); asyncio.run(tracker.refresh_inactive_kakao_status(get_settings())); assert len(asked) == kakao_status.LOOKUP_BATCH, len(asked)

# Scenario 4: 작품 하나의 조회가 예외로 실패해도 나머지는 계속한다
async def sometimes_boom(self, series_id):
    if series_id == 100: raise RuntimeError("이 작품만 이상")
    return (200, {"on_issue": "Y"})
kp.KakaoPageClient.fetch_series_status = sometimes_boom
repository.set_setting(kakao_status.LOOKUP_KEY, None)
asyncio.run(tracker.refresh_inactive_kakao_status(get_settings())); saved = kakao_status.get_lookup()
assert saved["100"]["http"] is None and saved["100"]["on_issue"] is None and len(saved) == kakao_status.LOOKUP_BATCH, len(saved)
print("4) 한 작품 실패 격리 OK")

# Scenario 6: "지금 전체 재동기화"용 — all_batches면 40개씩 끝까지 이어서 확인하고, 묶음 사이에 BATCH_REST_SECONDS만큼 쉰다(카카오에 몰리지 않게). 묶음마다 진행 상황을 알린다.
# 한 작품이 실패해도(그 결과는 저장돼 하루 동안 다시 안 묻는다) 무한히 돌지 않고 끝난다. 기본 호출(all_batches 없음)은 예전처럼 한 묶음만
for i in range(150, 200): repository.upsert_new_kakao_webtoon(i, f"더많음{i}", status=repository.STATUS_EXCLUDED)
async def any_status(self, series_id): asked.append(series_id); return (200, {"on_issue": "Y"})
kp.KakaoPageClient.fetch_series_status = any_status
sleeps = []; _real_sleep = asyncio.sleep
async def record_sleep(seconds, *a, **k): sleeps.append(seconds); await _real_sleep(0)
tracker.asyncio.sleep = record_sleep
kakao_status.BATCH_REST_SECONDS = 7; kakao_status.LOOKUP_PAUSE_SECONDS = 0
inactive = [r["title_id"] for r in repository.get_kakao_webtoons_map().values() if r["status"] != repository.STATUS_ACTIVE]
repository.set_setting(kakao_status.LOOKUP_KEY, None); asked.clear(); messages = []
resolved = asyncio.run(asyncio.wait_for(tracker.refresh_inactive_kakao_status(get_settings(), all_batches=True, progress=messages.append), 10))
total = len(inactive)
assert sorted(asked) == sorted(inactive) and resolved == total, (len(asked), total, resolved)                                              # 구독 중이 아닌 작품 전부를 끝까지
batches = -(-total // kakao_status.LOOKUP_BATCH)
assert [x for x in sleeps if x == 7] == [7] * (batches - 1), f"묶음 사이에만 쉰다({batches - 1}번): {sleeps.count(7)}"
assert len(messages) == batches and f"{kakao_status.LOOKUP_BATCH}/{total}" in messages[0] and messages[-1].count(f"{total}/{total}") == 1, messages
sleeps.clear(); asked.clear(); repository.set_setting(kakao_status.LOOKUP_KEY, None)
asyncio.run(tracker.refresh_inactive_kakao_status(get_settings())); assert len(asked) == kakao_status.LOOKUP_BATCH and 7 not in sleeps, "기본은 한 묶음만(쉬지 않음)"
async def always_boom(self, series_id): raise RuntimeError("전부 실패")
kp.KakaoPageClient.fetch_series_status = always_boom
repository.set_setting(kakao_status.LOOKUP_KEY, None); sleeps.clear(); messages.clear()
failed = asyncio.run(asyncio.wait_for(tracker.refresh_inactive_kakao_status(get_settings(), all_batches=True, progress=messages.append), 10))
assert failed == 0 and len(kakao_status.get_lookup()) == total and len(messages) == batches, "전부 실패해도 끝난다(실패도 기록돼 다시 안 묻는다)"
tracker.asyncio.sleep = _real_sleep
print("6) 전체 재동기화용 끝까지 이어 확인 OK")

# Scenario 5: 제외됨/구독해제 목록 API가 완결 또는 서비스 종료를 is_finished로 알려 준다(화면이 가린다)
repository.set_setting(kakao_status.LOOKUP_KEY, None); kakao_status.save(11, on_issue="N", http=200); kakao_status.save(13, on_issue=None, http=404); kakao_status.save(12, on_issue="Y", http=200)
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        rows = {w["title_id"]: w for w in (await c.get("/api/kakao-webtoons", params={"status": "excluded"})).json()}
        assert rows[11]["is_finished"] is True and rows[12]["is_finished"] is False
        rows = {w["title_id"]: w for w in (await c.get("/api/kakao-webtoons", params={"status": "unsubscribed"})).json()}
        assert rows[13]["is_finished"] is True, "서비스 종료도 가린다"
asyncio.run(api())
print("5) API OK")
print("ALL OK")
