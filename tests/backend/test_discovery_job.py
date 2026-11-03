"""신작 스캔 작업(scheduler/discovery_job.py) — 여덟 단계를 차례로 돌리고, 한 단계가 실패해도 나머지는 계속하며, 하나라도 실패하면 "오류"로 끝난다.
겹치는 실행은 건너뛰고, 새로 완결된 작품은 디스코드 봇으로 확인을 요청한다(실패해도 다른 작품 알림은 계속). 카카오 목록 정기 갱신은 카카오 관리가 켜져 있을 때만.
"""
import asyncio

from app import db, discord_bot, job_status, kakao_catalog, new_episode_watch, repository, tracker
db.get_connection()
import app.scheduler.discovery_job as dj

calls, boom = [], set()
def step(name, result):
    async def fake(*a, **k):
        calls.append(name)
        if name in boom: raise RuntimeError(f"{name} 실패")
        return result
    return fake
STEPS = {  # 단계 이름: (가짜를 심을 곳, 돌려줄 값, 로그에 나올 "시작" 문구)
    "exclude_daily_plus": (tracker, "exclude_auto_added_daily_plus", [], "매일+ 작품 정리"),
    "thumbnails": (tracker, "backfill_missing_thumbnails", 3, "썸네일"),
    "authors": (tracker, "scan_subscriptions_for_updates", None, "작가 기반 신작 스캔"),
    "unlinked": (tracker, "discover_titles_for_unlinked_watched_authors", ["a", "b"], "미구독 작가 신작 스캔"),
    "tags": (tracker, "scan_curation_tags", None, "태그 기반 신작 스캔"),
    "inactive": (tracker, "refresh_inactive_metadata", 5, "구독해제/제외됨 정보 갱신"),
    "kakao_inactive": (tracker, "refresh_inactive_kakao_status", 3, "카카오 구독해제/제외됨 상태 확인"),
    "new_episodes": (new_episode_watch, "run", 2, "새 에피소드 확인"),
    "kakao_authors": (tracker, "scan_kakao_authors_for_new_titles", 4, "카카오웹툰 작가 신작 스캔"),
}
for name, (module, attr, result, _) in STEPS.items(): setattr(module, attr, step(name, result))
notified_calls = []
async def fake_notify(): notified_calls.append("notify")
dj._notify_newly_finished_real = dj._notify_newly_finished                                                                          # 완결 알림 자체는 아래에서 따로 확인
dj._notify_newly_finished = fake_notify
repository.set_setting("kakao_webtoons_enabled", "1")                                                                                  # 카카오 관리가 켜져 있을 때의 전체 흐름(꺼진 경우는 시나리오 7)
run = lambda: asyncio.run(dj.run_discovery_job())
status = lambda: job_status.snapshot()["discovery"]
log_text = lambda: "\n".join(status()["log"])
def reset(): calls.clear(); boom.clear(); notified_calls.clear()

# Scenario 1: 모두 성공 — 여덟 단계를 이 순서로 돌리고 완결 알림까지 하고 "성공"으로 끝난다. 로그에 단계별 결과 개수가 남는다
reset(); run()
assert calls == list(STEPS), calls
assert status()["status"] == "success" and notified_calls == ["notify"]
for fragment in ("썸네일 3개 채움", "미구독 작가 신작 스캔 완료 (2건 추가)", "구독해제/제외됨 5개 정보 갱신 완료", "카카오 구독해제/제외됨 3개 상태 확인 완료", "새 에피소드 2건 알림", "카카오웹툰 신작 4건 자동 추가"): assert fragment in log_text(), fragment
print("1) 모두 성공 OK")

# Scenario 2: 단계 하나가 실패해도 나머지는 계속하고(오류 문구가 로그에), 마지막에 "오류"로 끝난다 — 단계마다 확인
for name in STEPS:
    reset(); boom.add(name); run()
    assert calls == list(STEPS), f"{name} 실패 뒤에도 나머지를 돌려야 한다: {calls}"
    assert status()["status"] == "error" and f"{name} 실패" in log_text() and notified_calls == ["notify"], (name, status()["status"])
print("2) 단계별 실패 격리 OK")

# Scenario 3: 자동 추가된 매일+ 작품을 정리했으면 그 이름을 로그에 남긴다
reset(); tracker.exclude_auto_added_daily_plus = step("exclude_daily_plus", [("1", "매일플러스작"), ("2", "다른작")]); run()
assert "자동 추가된 매일+ 작품 2개를 제외됨으로 정리했습니다: 매일플러스작, 다른작" in log_text()
tracker.exclude_auto_added_daily_plus = step("exclude_daily_plus", [])
print("3) 매일+ 정리 로그 OK")

# Scenario 4: 완결 알림 처리가 실패해도 작업은 끝나고(오류로), 이미 실행 중이면 겹쳐 돌리지 않고 건너뛴다
async def bad_notify(): raise RuntimeError("봇 오류")
dj._notify_newly_finished = bad_notify; reset(); run(); assert status()["status"] == "error" and calls == list(STEPS)
dj._notify_newly_finished = fake_notify
async def overlap():
    async with dj._discovery_job_lock:
        await dj.run_discovery_job()
reset(); asyncio.run(asyncio.wait_for(overlap(), 3)); assert calls == [] and "이미 실행 중이라 건너뜁니다" in log_text(), (calls, log_text())
print("4) 알림 실패/중복 실행 방지 OK")

# Scenario 5: 새로 완결된 작품 알림 — 구독 중이고 완결이면서 아직 알리지도 확인받지도 않은 것만 봇으로 보내고 "알림 기록"을 남긴다. 한 작품이 실패해도 다음 작품은 보낸다
dj._notify_newly_finished = dj._notify_newly_finished_real
kakao_notified = []
async def fake_kakao_notify(): kakao_notified.append(1)
dj._notify_kakao_newly_finished = fake_kakao_notify
for tid, name in (("n1", "알릴작1"), ("n2", "알릴작2(전송 실패)"), ("n3", "알릴작3"), ("done", "이미 알림"), ("ack", "확인함"), ("live", "연재중"), ("unsub", "구독해제됨")):
    repository.upsert_new(tid, name)
    repository.set_status(tid, repository.STATUS_UNSUBSCRIBED if tid == "unsub" else repository.STATUS_ACTIVE)
for tid in ("n1", "n2", "n3", "done", "ack", "unsub"): repository.mark_finished(tid)
repository.set_finish_notified("done"); repository.acknowledge_finish("ack")
prompts = []
async def fake_prompt(title_id, title):
    prompts.append(title_id)
    if title_id == "n2": raise RuntimeError("디스코드 거부")
discord_bot.send_completion_prompt = fake_prompt
job_status.start("discovery"); asyncio.run(dj._notify_newly_finished())
assert sorted(prompts) == ["n1", "n2", "n3"] and kakao_notified == [1], (prompts, kakao_notified)                                  # 카카오 알림을 먼저 다시 시도하고, 대상만 보낸다
notified = {w.title_id: w.finish_notified for w in repository.list_by_status(repository.STATUS_ACTIVE)}
assert notified["n1"] and notified["n3"] and not notified["n2"], notified                                                              # 실패한 작품은 기록하지 않아 다음에 다시 시도한다
assert "완결 확인 알림 2건 전송" in log_text()
prompts.clear(); job_status.start("discovery"); asyncio.run(dj._notify_newly_finished())
assert prompts == ["n2"] and "완결 확인 알림 0건 전송" in log_text(), (prompts, log_text())                                              # 다음 실행에는 실패했던 것만
for tid in ("n2",): repository.set_finish_notified(tid)
job_status.start("discovery"); prompts.clear(); asyncio.run(dj._notify_newly_finished()); assert prompts == [] and "알림" not in log_text(), "보낼 것이 없으면 조용히 끝난다"
print("5) 완결 알림 OK")

# Scenario 6: 카카오 목록 정기 갱신 — 카카오 관리가 꺼져 있으면 아무것도 안 하고, 켜져 있으면 갱신하며, 실패해도 예외를 던지지 않는다
refreshed = []
async def fake_refresh():
    refreshed.append(1)
    if len(refreshed) == 2: raise RuntimeError("카카오 장애")
kakao_catalog.refresh = fake_refresh
repository.set_setting("kakao_webtoons_enabled", None); asyncio.run(dj.run_kakao_catalog_refresh_job()); assert refreshed == []
repository.set_setting("kakao_webtoons_enabled", "1"); asyncio.run(dj.run_kakao_catalog_refresh_job()); asyncio.run(dj.run_kakao_catalog_refresh_job()); assert refreshed == [1, 1]
print("6) 카카오 목록 정기 갱신 OK")
# Scenario 7: 카카오 관리가 꺼져 있으면 카카오 작품 상태 확인은 건너뛴다(나머지 단계는 그대로) — 쓰지 않는 기능으로 카카오에 요청하지 않는다
repository.set_setting("kakao_webtoons_enabled", None); reset(); run()
assert calls == [name for name in STEPS if name != "kakao_inactive"] and status()["status"] == "success", calls
print("7) 카카오 꺼짐 OK")
print("ALL OK")
