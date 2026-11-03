"""다운로드 작업(scheduler/download_job.py) — 네이버/카카오를 한 번에 돌리고, 같은 플랫폼의 겹치는 실행을 막고, 한 작품이 실패해도 계속하며, 이력 보관 기간을 정리한다.
실제 다운로드(네트워크)는 가짜로 바꾸고, 이 작업이 "무엇을 어떤 순서로 부르는지/실패를 어떻게 다루는지/무엇을 남기는지"를 확인한다.
"""
import asyncio

from app import cookie_health, daily_plus, db, job_status, repository, waitfree_sync
db.get_connection()
import app.scheduler.download_job as dj

real_sleep = asyncio.sleep
async def no_sleep(seconds, *a, **k): await real_sleep(0)
asyncio.sleep = no_sleep                                                                                                          # 작품 사이 대기(delay_seconds)를 건너뛴다

calls = {"naver": [], "kakao": [], "finalize": 0}; boom = set(); state = {"kakao_fail": False, "cookie_fail": False}
async def fake_naver(session, settings, title_id, adult_tracker, failures):
    calls["naver"].append(title_id)
    if title_id in boom: raise RuntimeError(f"{title_id} 다운로드 오류")
async def fake_kakao(settings, failures, *, skip_waiting):
    calls["kakao"].append(skip_waiting)
    if state["kakao_fail"]: raise RuntimeError("카카오 장애")
async def fake_finalize(session, tracker):
    calls["finalize"] += 1
    if state["cookie_fail"]: raise RuntimeError("쿠키 판단 오류")
dj._download_new_episodes_for_one, dj._download_kakao_subscriptions = fake_naver, fake_kakao
cookie_health.finalize_and_notify = fake_finalize
run = lambda target="naver", scheduled=False: asyncio.run(dj.run_download_job(target, scheduled))
snap = lambda: job_status.snapshot()["download"]
text = lambda: "\n".join(snap()["log"])
def reset():
    calls.update(naver=[], kakao=[], finalize=0); boom.clear(); state.update(kakao_fail=False, cookie_fail=False)
    dj._download_claimed_platforms.clear(); repository.set_setting("job_history_retention_days", None); repository.set_setting("episode_history_retention_days", None)

for tid, name in (("1", "첫째"), ("2", "둘째"), ("3", "구독해제됨")):
    repository.upsert_new(tid, name); repository.set_status(tid, repository.STATUS_UNSUBSCRIBED if tid == "3" else repository.STATUS_ACTIVE)
daily_plus.cached_ids = lambda: set()

# Scenario 1: 네이버 실행 — 구독 중인 작품만 차례로, 그 뒤 쿠키 상태 확인(한 번), 카카오는 안 부른다. 성공으로 끝나고 시작/종료 로그가 남는다
reset(); run("naver")
assert sorted(calls["naver"]) == ["1", "2"] and calls["kakao"] == [] and calls["finalize"] == 1, calls                          # (순서는 저장소가 정한다 — 구독 중인 둘만)
assert snap()["status"] == "success" and "구독 중인 네이버 웹툰 2개" in text() and text().endswith("다운로드 스캔 종료"), text()
print("1) 네이버 실행 OK")

# Scenario 2: 작품 하나가 예외로 실패해도 다음 작품을 계속하고, 로그와 "실패 N건" 요약을 남기며 "오류"로 끝난다
reset(); boom.add("1"); run("naver")
assert sorted(calls["naver"]) == ["1", "2"] and snap()["status"] == "error", calls
assert "[첫째] 처리 중 오류: 1 다운로드 오류" in text() and "실패 1건: 첫째(1 다운로드 오류)" in text(), text()
print("2) 작품 실패 격리 OK")

# Scenario 3: 카카오 — 대상이 kakao면 네이버는 건너뛰고(로그 "카카오페이지만"), both면 둘 다. 기다무 충전 시각 기능은 "정기 스케줄 + 옵션 켬"일 때만 충전 전 작품을 건너뛴다(수동 실행은 항상 전부)
reset(); run("kakao"); assert calls == {"naver": [], "kakao": [False], "finalize": 1} and "카카오페이지만" in text(), calls
reset(); run("both"); assert sorted(calls["naver"]) == ["1", "2"] and calls["kakao"] == [False]
waitfree_sync.set_enabled(True)
reset(); run("kakao", scheduled=True); assert calls["kakao"] == [True], "정기 스케줄 + 옵션 켬 → 충전 전 작품 건너뜀"
reset(); run("kakao", scheduled=False); assert calls["kakao"] == [False], "수동 실행은 항상 전부 확인"
waitfree_sync.set_enabled(False)
reset(); run("kakao", scheduled=True); assert calls["kakao"] == [False], "옵션이 꺼져 있으면 정기 실행도 전부"
print("3) 대상/기다무 옵션 OK")

# Scenario 4: 카카오 실패는 오류로 끝나고(로그), 쿠키 상태 확인 실패는 작업을 오류로 만들지 않는다
reset(); state["kakao_fail"] = True; run("both"); assert snap()["status"] == "error" and "카카오페이지 자동 다운로드 중 오류: 카카오 장애" in text()
reset(); state["cookie_fail"] = True; run("naver"); assert snap()["status"] == "success" and sorted(calls["naver"]) == ["1", "2"]
print("4) 카카오/쿠키 판단 실패 OK")

# Scenario 5: 겹치는 실행 — 같은 플랫폼을 이미 맡은 실행이 있으면 건너뛰고, 일부만 겹치면 나머지만 실행한다. 맡은 플랫폼은 끝나면(예외로 끝나도) 풀린다. 알 수 없는 대상은 네이버로
reset(); dj._download_claimed_platforms.add("naver"); run("naver"); assert calls["naver"] == [] and "이미 실행 중이거나 대기 중이라 건너뜁니다" in text()
reset(); dj._download_claimed_platforms.add("naver"); run("both"); assert calls == {"naver": [], "kakao": [False], "finalize": 1} and "나머지만 실행합니다" in text(), calls
assert dj._download_claimed_platforms == {"naver"}, "다른 실행이 맡은 것은 그대로, 이 실행이 맡았던 것만 풀린다"
reset(); run("이상한대상"); assert sorted(calls["naver"]) == ["1", "2"]
async def crash(platforms, scheduled=False, partial=False): raise RuntimeError("작업 자체가 죽음")
real_impl = dj._run_download_job_impl; dj._run_download_job_impl = crash; reset()
try: run("naver")
except RuntimeError: pass
assert dj._download_claimed_platforms == set(), "예외로 끝나도 맡은 플랫폼이 풀려야 다음 실행이 막히지 않는다"
dj._run_download_job_impl = real_impl
print("5) 중복 실행 방지 OK")

# Scenario 6: 신작 스캔이 자동 추가한 매일+ 작품은 받기 전에 제외됨으로 정리하고 이름을 로그에 남긴다(받지 않는다)
excluded_ids = []
def fake_exclude(ids): excluded_ids.append(set(ids)); repository.set_status("2", repository.STATUS_EXCLUDED); return [("2", "둘째")]
real_exclude = repository.exclude_auto_added_titles; repository.exclude_auto_added_titles = fake_exclude; daily_plus.cached_ids = lambda: {"2"}
reset(); run("naver"); assert excluded_ids == [{"2"}] and calls["naver"] == ["1"] and "자동 추가된 매일+ 작품 1개를 제외됨으로 정리했습니다: 둘째" in text(), (excluded_ids, calls, text())
repository.set_status("2", repository.STATUS_ACTIVE); repository.exclude_auto_added_titles = real_exclude; daily_plus.cached_ids = lambda: set()
print("6) 매일+ 정리 OK")

# Scenario 7: 이력 보관 기간 — 설정이 없거나 0 이하면 아무것도 안 지우고, 양수면 그 일수로 지우고 개수를 로그에 남긴다. 정리가 실패해도 작업은 성공
deleted = {"job": [], "episode": []}
repository.delete_job_history_older_than = lambda days: (deleted["job"].append(days), 4)[1]
repository.delete_episode_history_older_than = lambda days: (deleted["episode"].append(days), 0)[1]
for raw in (None, "", "0", "-3"):
    reset(); deleted.update(job=[], episode=[])
    repository.set_setting("job_history_retention_days", raw); repository.set_setting("episode_history_retention_days", raw); run("naver")
    assert deleted == {"job": [], "episode": []}, (raw, deleted)
reset(); deleted.update(job=[], episode=[]); repository.set_setting("job_history_retention_days", "30"); repository.set_setting("episode_history_retention_days", "90"); run("naver")
assert deleted == {"job": [30], "episode": [90]} and "보관기간(30일) 초과 실행 이력 4건 정리" in text() and "초과 다운로드 이력" not in text(), (deleted, text())   # 0건이면 로그 없음
def broken(days): raise RuntimeError("DB 잠김")
repository.delete_job_history_older_than = broken; repository.delete_episode_history_older_than = broken
reset(); repository.set_setting("job_history_retention_days", "30"); repository.set_setting("episode_history_retention_days", "90"); run("naver"); assert snap()["status"] == "success"
print("7) 이력 정리 OK")

# Scenario 8: 실패 한 줄 요약 — 회차가 있으면 "작품 12화", 없으면 "작품(이유)"
assert dj._failure_label({"title_name": "작품", "episode_no": 12, "subtitle": "x"}) == "작품 12화" and dj._failure_label({"title_name": "작품", "episode_no": None, "subtitle": "로그인 필요"}) == "작품(로그인 필요)"
print("ALL OK")
