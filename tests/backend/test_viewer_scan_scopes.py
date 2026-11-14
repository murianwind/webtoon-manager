"""뷰어 스캔을 저장소 범위별로 시킨다 — 다운로드(로컬 폴더에 새 파일)는 로컬(fast)만, 아카이빙·일괄 이동(원격 드라이브로 옮김)은 로컬+원격(all).

왜: 뷰어가 전체 스캔 하나만 돌릴 수 있을 때는 아카이빙 뒤 30분짜리 원격 스캔이 도는 동안 다운로드 리포트가 그 스캔을 기다려 늦어졌다.
이제 뷰어(webtoon-server)는 범위(scope)를 받아 로컬과 원격을 서로 다른 작업으로 동시에 돌리므로, 여기서도 두 레인을 따로 추적한다:
- 레인마다 요청/시작/끝을 따로 기록하고, 한 번의 확인(tick)에서 레인마다 상태 조회 한 번·시작 요청 한 번 이하.
- 다운로드 리포트는 로컬 레인만 기다린다(원격 스캔이 돌고 있어도 보낸다).
- 뷰어가 범위를 모르는 옛 버전이면(응답에 scope가 없다) 전체 스캔 하나로 합쳐서 예전처럼 동작한다.
- 실행 이력의 줄에는 어느 저장소인지([로컬]/[원격]/[전체]) 붙는다.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app import db, discord_config, job_status, repository, report_dispatch as rd, viewer_scan, webtoon_server_client
db.get_connection()
from app.scheduler import report_job

UTC = timezone.utc
T0 = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
MIN = lambda n: timedelta(minutes=n)
SCAN = {}
async def fake_start(session, base, token, timeout, scope):
    SCAN["start"].append(scope); return SCAN["start_result"], SCAN["supports_scope"]
async def fake_status(session, base, token, timeout, scope):
    SCAN["poll"].append(scope); return {"running": SCAN["running"][scope] if SCAN["supports_scope"] else SCAN["running"]["fast"]}
webtoon_server_client.start_scan, webtoon_server_client.scan_status = fake_start, fake_status
logs = []
viewer_scan.job_status.log_line = lambda job, line: logs.append(line)


def reset(*, supports_scope=True):
    SCAN.update(start=[], poll=[], start_result="started", supports_scope=supports_scope, running={"fast": True, "slow": True}); logs.clear()
    repository.set_setting(viewer_scan.STATE_KEY, None)
    repository.set_setting("webtoon_server_url", "http://viewer"); viewer_scan.set_config(enabled=True, token="secret-token", max_wait_minutes=45)
tick = lambda now: asyncio.run(viewer_scan.tick(None, now, 5))
lane = lambda name: viewer_scan.load_state()["lanes"][name]

# Scenario 1: 다운로드는 로컬(fast)만 — 요청 → 시작(scope=fast) → 로컬 레인만 상태 확인 → 끝. 원격 레인은 건드리지 않는다
reset(); viewer_scan.request(T0, scope="fast", reason="다운로드 2건 완료")
assert viewer_scan.covered_state(T0, "fast") == "waiting" and viewer_scan.covered_state(T0, "slow") == "failed", "원격에는 요청한 것이 없다"
tick(T0 + MIN(1)); assert SCAN["start"] == ["fast"] and SCAN["poll"] == [] and lane("fast")["started_at"] and lane("slow")["started_at"] is None
tick(T0 + MIN(2)); assert SCAN["poll"] == ["fast"], "원격 레인은 조회하지 않는다"
SCAN["running"]["fast"] = False; tick(T0 + MIN(3)); assert viewer_scan.covered_state(T0, "fast") == "done" and not viewer_scan.has_work()
assert viewer_scan.covered_state(T0, "all") == "failed", "로컬+원격으로 물으면 원격은 스캔한 적이 없으니 다 반영된 것이 아니다"
assert any(l == "스캔 필요[로컬]: 다운로드 2건 완료" for l in logs) and any(l.startswith("[로컬] 웹툰 서버에 스캔을 시작시켰습니다") for l in logs) and any("[로컬] 스캔이 끝났습니다" in l for l in logs), logs
print("1) 다운로드는 로컬만 OK")

# Scenario 2: 아카이빙은 로컬+원격(all) — 두 레인을 같은 확인에서 모두 시작하고, 로컬이 먼저 끝나도 원격은 계속 추적한다
reset(); viewer_scan.note_library_changed(3, T0, source="정기 아카이빙")
assert any(l == "스캔 필요[로컬+원격]: 정기 아카이빙에서 파일 3개를 옮김" for l in logs), logs
tick(T0 + MIN(1)); assert SCAN["start"] == ["fast", "slow"]
tick(T0 + MIN(2)); assert SCAN["poll"] == ["fast", "slow"], "레인마다 한 번씩"
SCAN["running"]["fast"] = False; tick(T0 + MIN(3))
assert viewer_scan.covered_state(T0, "fast") == "done" and viewer_scan.covered_state(T0, "slow") == "waiting" and viewer_scan.covered_state(T0, "all") == "waiting"
assert viewer_scan.has_work() and job_status.snapshot()["viewer_server"]["status"] == "running", "원격이 도는 동안 주기는 계속된다"
tick(T0 + MIN(4)); assert SCAN["poll"][-1] == "slow" and SCAN["poll"].count("fast") == 2, "끝난 로컬 레인은 더 조회하지 않는다"
SCAN["running"]["slow"] = False; tick(T0 + MIN(30)); assert viewer_scan.covered_state(T0, "all") == "done" and not viewer_scan.has_work()
assert any("[원격] 스캔이 끝났습니다 (시작 후 29분)" in l for l in logs), logs
print("2) 아카이빙은 로컬+원격 OK")

# Scenario 3: 원격 스캔이 도는 동안 다운로드가 끝나면 로컬 스캔은 기다리지 않고 바로 시작하고 먼저 끝난다 — 이것이 리포트가 빨라지는 이유
reset(); viewer_scan.note_library_changed(1, T0, source="정기 아카이빙"); tick(T0 + MIN(1)); SCAN["running"]["fast"] = False; tick(T0 + MIN(3))
assert viewer_scan.covered_state(T0, "fast") == "done" and viewer_scan.covered_state(T0, "slow") == "waiting"
viewer_scan.request(T0 + MIN(5), scope="fast", reason="다운로드 1건 완료"); SCAN["running"]["fast"] = True
tick(T0 + MIN(6)); assert SCAN["start"] == ["fast", "slow", "fast"], SCAN["start"]                                        # 원격이 도는 중인데도 로컬은 새로 시작
assert viewer_scan.covered_state(T0 + MIN(5), "fast") == "waiting"
SCAN["running"]["fast"] = False; tick(T0 + MIN(8)); assert viewer_scan.covered_state(T0 + MIN(5), "fast") == "done" and viewer_scan.covered_state(T0, "slow") == "waiting"
print("3) 원격 스캔 중에도 로컬 스캔은 바로 OK")

# Scenario 4: 옛 뷰어(범위를 모른다: 응답에 scope 없음)는 전체 스캔 하나로 합친다 — 로컬+원격 요청에도 시작은 한 번, 조회도 한 번, 어느 범위를 물어도 같은 스캔으로 답한다
reset(supports_scope=False); viewer_scan.note_library_changed(2, T0, source="정기 아카이빙"); tick(T0 + MIN(1))
assert SCAN["start"] == ["fast"], "첫 시작에서 범위를 모른다는 것을 알고 나머지 레인은 그 스캔에 합친다"
assert viewer_scan.load_state()["legacy"] is True and lane("slow")["requested_at"] is None
tick(T0 + MIN(2)); assert SCAN["poll"] == ["all"], "조회도 한 번(전체 스캔 기준)"
viewer_scan.request(T0 + MIN(3), scope="slow", reason="원격만"); tick(T0 + MIN(4)); assert len(SCAN["start"]) == 1, "도는 중이면 끝난 뒤(전체 스캔 하나)"
SCAN["running"]["fast"] = False; tick(T0 + MIN(5)); assert SCAN["start"] == ["fast", "all"] and viewer_scan.covered_state(T0, "slow") == "done" and viewer_scan.covered_state(T0, "all") == "done"   # 옛 뷰어임을 안 뒤에는 all로 시킨다(옛 뷰어는 무시하고 전체를 훑는다)
SCAN["running"]["fast"] = False; tick(T0 + MIN(6)); assert viewer_scan.covered_state(T0 + MIN(3), "slow") == "done" and viewer_scan.covered_state(T0 + MIN(3), "fast") == "done"
assert any(l.startswith("[전체] 웹툰 서버에 스캔을 시작시켰습니다") for l in logs), logs
# 뷰어를 새 버전으로 바꾸면(응답에 scope가 다시 들어옴) 그 다음 요청부터 범위별로 돌아간다
SCAN["supports_scope"] = True; viewer_scan.note_library_changed(1, T0 + MIN(10)); tick(T0 + MIN(11)); assert SCAN["start"][-1] == "all" and viewer_scan.load_state()["legacy"] is False
SCAN["running"]["fast"] = False; tick(T0 + MIN(12)); viewer_scan.note_library_changed(1, T0 + MIN(13)); tick(T0 + MIN(14)); assert SCAN["start"][-2:] == ["fast", "slow"], SCAN["start"]
print("4) 옛 뷰어는 전체 스캔 하나로 OK")

# Scenario 5: 예전 모양으로 저장된 상태(레인 없이 requested_at 등이 맨 위에 있음)가 남아 있어도 깨지지 않고 빈 상태로 시작한다
reset(); repository.set_setting(viewer_scan.STATE_KEY, json.dumps({"requested_at": T0.isoformat(), "started_at": None, "finished": True, "watermark": "2026-01-01T00:00:00+00:00"}))
assert not viewer_scan.has_work() and viewer_scan.load_state()["watermark"] == "2026-01-01T00:00:00+00:00" and set(viewer_scan.load_state()["lanes"]) == {"fast", "slow"}
tick(T0 + MIN(1)); assert SCAN["start"] == []
print("5) 옛 상태 호환 OK")

# Scenario 6: 다운로드 리포트는 로컬 레인만 기다린다 — 원격 스캔(아카이빙)이 도는 중이라도 로컬 스캔이 끝나면 바로 보낸다. 묶음 확정 때의 스캔 요청은 scope=fast다
from app import discord_notify, scheduler
from fakes import discord_fake
sent = []; discord_notify.send_webhook_notification = discord_fake(sent, result=None)
from app import list_alert; list_alert.should_attach = lambda now: False
async def _no_new(session, settings): return []
scheduler.report_sections.collect_naver_new_titles = scheduler.report_sections.collect_kakao_new_titles = _no_new
async def fake_sections(session, settings, *, force_test): return [], []
scheduler.report_job.collect_new_episode_sections = fake_sections
async def fake_reader(session, base, name, timeout): return None
webtoon_server_client.fetch_reader_url = fake_reader
LAST_SENT = report_job._SETTING_KEY_REPORT_LAST_SENT_AT
_seq = [0]
def add_row(name, when):
    _seq[0] += 1; repository.add_episode_history(f"t{_seq[0]}", name, _seq[0], f"{_seq[0]}화", "success", platform="naver")
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE id = (SELECT MAX(id) FROM episode_history)", (when.isoformat(),))
def reset_report():
    reset(); sent.clear(); repository.set_setting(rd.STATE_KEY, None); repository.set_setting(LAST_SENT, (T0 - timedelta(days=1)).isoformat()); rd.set_quiet_hours([]); rd.set_batch_minutes(10)
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
rtick = lambda now: asyncio.run(scheduler.run_report_tick(now))
reset_report()
viewer_scan.note_library_changed(1, T0, source="정기 아카이빙"); rtick(T0 + MIN(1)); SCAN["running"]["fast"] = False; rtick(T0 + MIN(2))          # 원격 스캔이 T0+1부터 계속 도는 중
assert viewer_scan.covered_state(T0, "slow") == "waiting" and viewer_scan.covered_state(T0, "fast") == "done"
add_row("새회차", T0 + MIN(5)); rd.note_download_finished(T0 + MIN(5)); SCAN["running"]["fast"] = True
rtick(T0 + MIN(15)); assert SCAN["start"] == ["fast", "slow", "fast"] and sent == [], (SCAN["start"], sent)                                       # 묶음 확정 → 로컬 스캔만 새로 요청, 끝나기 전엔 안 보냄
rtick(T0 + MIN(16)); assert sent == []
SCAN["running"]["fast"] = False; rtick(T0 + MIN(17))
assert len(sent) == 1 and "새회차" in sent[0], (sent, SCAN)                                                                                         # 원격 스캔은 아직 도는데도 리포트는 나간다
assert viewer_scan.covered_state(T0, "slow") == "waiting" and viewer_scan.has_work()
# 로컬 스캔이 도는 중에 아카이빙이 끝나(로컬+원격 요청) 원격 스캔이 새로 시작돼도, 리포트는 다운로드 뒤에 시작한 로컬 스캔이 끝나면 바로 나간다(원격은 기다리지 않는다)
reset_report(); add_row("회차2", T0); rd.note_download_finished(T0); rtick(T0 + MIN(10)); assert SCAN["start"] == ["fast"]
viewer_scan.note_library_changed(1, T0 + MIN(11), source="정기 아카이빙"); rtick(T0 + MIN(12)); assert SCAN["start"] == ["fast", "slow"] and sent == []
SCAN["running"]["fast"] = False; rtick(T0 + MIN(13)); assert len(sent) == 1 and "회차2" in sent[0], (sent, SCAN)
assert SCAN["start"] == ["fast", "slow", "fast"], "아카이빙 때문에 로컬도 한 번 더 훑지만 리포트는 그것을 기다리지 않는다"
print("6) 리포트는 로컬 스캔만 기다림 OK")

# Scenario 7: 묶음 확정 때 로컬 스캔을 못 시키면(연동 미지원/연결 오류) 리포트는 기다리지 않는다 — 원격 레인 상태와 무관
reset_report(); SCAN["start_result"] = "error"
add_row("오류", T0); rd.note_download_finished(T0); rtick(T0 + MIN(10)); assert len(sent) == 1 and SCAN["start"] == ["fast"], (sent, SCAN)
print("7) 로컬 스캔 실패면 바로 발송 OK")
print("ALL OK")
