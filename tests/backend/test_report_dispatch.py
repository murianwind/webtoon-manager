"""다운로드 리포트 발송 흐름 — 다운로드가 끝나면(N분 모아서) 뷰어 스캔 → 리포트. 발송하지 않는 시간에는 쌓아 두었다가 끝나면 한 번에.

1분마다 도는 확인(run_report_tick)이 저장된 상태만 보고 움직인다(서버 요청은 스캔/발송할 때만):
  다운로드 끝남(마지막 다운로드 시각 기록) → N분 동안 새 다운로드가 없으면 묶음 확정(cutoff) → 보낼 이력이 있으면 뷰어 스캔(시작 후 끝날 때까지 기다림,
  최대 대기 시간이 지나거나 뷰어가 지원하지 않으면 그냥 진행) → 발송하지 않는 시간이면 끝날 때까지 대기 → 리포트 발송(cutoff까지의 이력 + 새 에피소드 요약).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db, discord_config, discord_notify, job_status, repository, report_dispatch as rd, scheduler, viewer_scan, webtoon_server_client
from fakes import discord_fake
db.get_connection()
# 이 테스트는 리포트에 담기는 섹션의 내용/반복 여부를 본다 — 언제 얹히는지(하루 한 번)는 test_list_alert.py/test_list_alert_flow.py가 본다. 그래서 매 묶음에 얹히게 둔다.
from app import list_alert
list_alert.should_attach = lambda now: True
import app.scheduler as _scheduler                                                                                 # 신규 웹툰 수집은 이 테스트의 관심사가 아니라 비워 둔다(실제 네트워크를 부르지 않게)
async def _no_new_titles(session, settings): return []
_scheduler.report_sections.collect_naver_new_titles = _no_new_titles; _scheduler.report_sections.collect_kakao_new_titles = _no_new_titles

KST, UTC = ZoneInfo("Asia/Seoul"), timezone.utc
T0 = datetime(2026, 10, 13, 3, 0, tzinfo=KST)          # 화요일 새벽 3시(발송하지 않는 시간 설정이 없을 때는 아무 때나)
MIN = lambda n: timedelta(minutes=n)
LAST_SENT = "report_last_sent_at"

sent = []; SCAN = {}; SECTIONS = {"naver": [], "kakao": []}; VIEWER = {}
fake_send = discord_fake(sent, result=None)
discord_notify.send_webhook_notification = fake_send
async def fake_start(session, base, token, timeout, scope): SCAN["start"].append((base, token)); SCAN["scopes"].append(scope); return SCAN["start_result"], True
async def fake_status(session, base, token, timeout, scope): SCAN["poll"] += 1; return {"running": SCAN["running"]}
webtoon_server_client.start_scan = fake_start; webtoon_server_client.scan_status = fake_status
async def fake_sections(session, settings, *, force_test): return list(SECTIONS["naver"]), list(SECTIONS["kakao"])
scheduler.report_job.collect_new_episode_sections = fake_sections
async def fake_reader(session, base, name, timeout): return VIEWER.get(name)
webtoon_server_client.fetch_reader_url = fake_reader
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")


def reset(*, quiet=(), batch=10, scan=False, max_wait=45):
    sent.clear(); SCAN.update(start=[], scopes=[], poll=0, start_result="started", running=True); SECTIONS.update(naver=[], kakao=[]); VIEWER.clear()
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
    repository.set_setting(rd.STATE_KEY, None); repository.set_setting(viewer_scan.STATE_KEY, None); repository.set_setting(LAST_SENT, (T0 - timedelta(days=1)).astimezone(UTC).isoformat())
    rd.set_quiet_hours(list(quiet)); rd.set_batch_minutes(batch)
    repository.set_setting("webtoon_server_url", "http://viewer" if scan else None); viewer_scan.set_config(enabled=scan, token="secret-token" if scan else "", max_wait_minutes=max_wait)
_seq = [0]
def add_row(name, when, status="success", platform="naver"):
    _seq[0] += 1          # 회차마다 다른 번호 — 이미 보고한 회차(report_seen)는 다시 보내지 않으므로 시나리오끼리 겹치지 않게
    repository.add_episode_history(f"t{_seq[0]}", name, _seq[0], f"{_seq[0]}화", status, platform=platform)
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE id = (SELECT MAX(id) FROM episode_history)", (when.astimezone(UTC).isoformat(),))
def tick(now): asyncio.run(scheduler.run_report_tick(now))
def state(): return rd.load_state()
quiet_night = {"start_hour": 23, "start_minute": 0, "end_hour": 6, "end_minute": 0, "days": []}

# Scenario 1: 기본 — 다운로드가 끝나고 N분(10분) 동안 새 다운로드가 없으면 그때 한 통(성공+실패). 그 전에는 보내지 않는다. 보낸 뒤 상태가 정리되고 발송 시각이 묶음 끝으로 간다
reset(); add_row("작품A", T0); add_row("작품B", T0, status="failed"); rd.note_download_finished(T0 + MIN(1))
tick(T0 + MIN(5)); assert sent == [], "묶음이 열려 있는 동안은 보내지 않는다"
tick(T0 + MIN(12)); assert len(sent) == 1 and "작품A" in sent[0] and "작품B" in sent[0] and "실패한 작품" in sent[0], sent
assert state()["last_download_at"] is None and state()["cutoff"] is None and repository.get_setting(LAST_SENT) == (T0 + MIN(1)).astimezone(UTC).isoformat()
tick(T0 + MIN(30)); assert len(sent) == 1, "보낸 뒤에는 또 보내지 않는다"
print("1) N분 모아서 한 통 OK")

# Scenario 2: 모으기 — 그 안에 또 다운로드가 끝나면 N분을 다시 센다. 두 번의 다운로드가 한 통에 들어간다
reset(); add_row("첫번째", T0); rd.note_download_finished(T0 + MIN(1)); add_row("두번째", T0 + MIN(7)); rd.note_download_finished(T0 + MIN(8))
tick(T0 + MIN(12)); assert sent == []
tick(T0 + MIN(19)); assert len(sent) == 1 and "첫번째" in sent[0] and "두번째" in sent[0]
reset(batch=0); add_row("바로", T0); rd.note_download_finished(T0); tick(T0); assert len(sent) == 1                       # 0분이면 바로(다음 확인 때)
print("2) 모으기/0분 OK")

# Scenario 3: 발송하지 않는 시간(23시~6시) — 그 사이 다운로드는 쌓아 두었다가 끝나면 한 통으로. 새벽에 받은 것도 같은 통에 들어간다. 6시 이후 다운로드는 바로(N분 뒤) 따로
at = lambda d, h, mi: datetime(2026, 10, d, h, mi, tzinfo=KST)
reset(quiet=[quiet_night]); add_row("밤에받음", at(13, 23, 10)); rd.note_download_finished(at(13, 23, 12))
tick(at(13, 23, 30)); tick(at(14, 3, 0)); assert sent == [], "발송하지 않는 시간에는 보내지 않는다"
add_row("새벽에받음", at(14, 5, 50)); rd.note_download_finished(at(14, 5, 55))
tick(at(14, 6, 0)); assert sent == [], "아직 묶음이 열려 있다(마지막 다운로드 05:55 + 10분)"
tick(at(14, 6, 6)); assert len(sent) == 1 and "밤에받음" in sent[0] and "새벽에받음" in sent[0], sent              # 한 통으로 몰아서
add_row("아침에받음", at(14, 6, 20)); rd.note_download_finished(at(14, 6, 20)); tick(at(14, 6, 31)); assert len(sent) == 2 and "아침에받음" in sent[1] and "밤에받음" not in sent[1]
print("3) 발송하지 않는 시간 쌓기/몰아 보내기 OK")

# Scenario 4: 뷰어 스캔 — 묶음이 확정되면 스캔을 한 번 시작하고 끝날 때까지 기다린 뒤 리포트(링크가 스캔 결과를 반영)
reset(scan=True); add_row("스캔작품", T0); VIEWER["스캔작품"] = "http://viewer/s"; rd.note_download_finished(T0)
tick(T0 + MIN(10)); assert SCAN["start"] == [("http://viewer", "secret-token")] and SCAN["scopes"] == ["fast"] and sent == [], "묶음 확정 때 로컬 저장소 스캔 시작, 끝나기 전에는 보내지 않는다"
tick(T0 + MIN(11)); tick(T0 + MIN(20)); assert len(SCAN["start"]) == 1 and SCAN["poll"] == 2 and sent == [], (SCAN, sent)        # 스캔은 한 번만 시작하고 진행 상태만 확인
SCAN["running"] = False; tick(T0 + MIN(25)); assert len(sent) == 1 and "http://viewer/s" in sent[0] and len(SCAN["start"]) == 1
assert state()["scan_started_at"] is None and state()["last_download_at"] is None
print("4) 스캔 끝나면 발송 OK")

# Scenario 5: 스캔이 너무 오래 걸리면(최대 대기 45분) 그냥 보낸다. 뷰어가 스캔 연동을 지원하지 않으면(unsupported) 기다리지 않는다. 스캔을 꺼 두면(기본) 요청하지 않는다
reset(scan=True, max_wait=45); add_row("오래걸림", T0); rd.note_download_finished(T0)
tick(T0 + MIN(10)); tick(T0 + MIN(40)); tick(T0 + MIN(54)); assert sent == [] and len(SCAN["start"]) == 1                                    # 스캔 시작 T0+10분 → 44분 경과까지 기다림
tick(T0 + MIN(56)); assert len(sent) == 1, "최대 대기 시간이 지나면 진행"
reset(scan=True); SCAN["start_result"] = "unsupported"; add_row("미지원", T0); rd.note_download_finished(T0); tick(T0 + MIN(10)); assert len(sent) == 1 and SCAN["poll"] == 0
reset(scan=False); add_row("스캔꺼짐", T0); rd.note_download_finished(T0); tick(T0 + MIN(10)); assert len(sent) == 1 and SCAN["start"] == []
reset(scan=True); viewer_scan.set_config(enabled=True, token="", max_wait_minutes=45); add_row("토큰없음", T0); rd.note_download_finished(T0)      # 토큰이 없으면 스캔은 못 한다 — 리포트는 그대로
tick(T0 + MIN(10)); assert len(sent) == 1 and SCAN["start"] == []
print("5) 스캔 시간 초과/미지원/꺼짐 OK")

# Scenario 6: 스캔은 발송하지 않는 시간과 상관없이 묶음이 확정될 때 하고, 리포트만 끝날 때까지 미룬다
reset(quiet=[quiet_night], scan=True); add_row("밤스캔", at(13, 23, 10)); rd.note_download_finished(at(13, 23, 10))
tick(at(13, 23, 20)); assert len(SCAN["start"]) == 1; SCAN["running"] = False; tick(at(13, 23, 40)); assert sent == [] and state()["scan_done"] is True
tick(at(14, 5, 59)); assert sent == []; tick(at(14, 6, 0)); assert len(sent) == 1 and "밤스캔" in sent[0] and len(SCAN["start"]) == 1
print("6) 스캔은 바로, 리포트는 끝난 뒤 OK")

# Scenario 7: 스캔이 끝난 뒤 발송을 기다리는 중(발송하지 않는 시간)에 새 다운로드가 끝나면 묶음을 다시 열어서 한 통으로 보낸다(다시 스캔). 스캔 진행 중이면 합치지 않고 다음 묶음으로
reset(quiet=[quiet_night], scan=True); add_row("앞", at(13, 23, 10)); rd.note_download_finished(at(13, 23, 10)); tick(at(13, 23, 20)); SCAN["running"] = False; tick(at(13, 23, 40))
add_row("뒤", at(14, 5, 50)); rd.note_download_finished(at(14, 5, 55)); assert state()["cutoff"] is None and state()["scan_done"] is False and state()["scan_started_at"] is None
SCAN["running"] = True; tick(at(14, 6, 6)); assert len(SCAN["start"]) == 2; SCAN["running"] = False; tick(at(14, 6, 8)); assert len(sent) == 1 and "앞" in sent[0] and "뒤" in sent[0], sent
reset(scan=True); add_row("진행중앞", T0); rd.note_download_finished(T0); tick(T0 + MIN(10)); assert len(SCAN["start"]) == 1                     # 스캔 진행 중
add_row("진행중뒤", T0 + MIN(12)); rd.note_download_finished(T0 + MIN(13)); SCAN["running"] = False; tick(T0 + MIN(14))
assert len(sent) == 1 and "진행중앞" in sent[0] and "진행중뒤" not in sent[0], sent                                                          # 진행 중이던 묶음만 먼저
assert state()["last_download_at"] == (T0 + MIN(13)).astimezone(UTC).isoformat() and state()["cutoff"] is None                              # 뒤의 다운로드는 다음 묶음으로 남는다
tick(T0 + MIN(24)); assert len(SCAN["start"]) == 2; tick(T0 + MIN(26)); assert len(sent) == 2 and "진행중뒤" in sent[1]
print("7) 대기 중 새 다운로드 합치기/스캔 중에는 다음 묶음 OK")

# Scenario 8: 보낼 이력이 없으면(새 에피소드 요약만 있어도) 스캔도 발송도 하지 않는다 — 요약은 다운로드 리포트가 나갈 때 같이 들어간다. 웹훅이 없으면 보내지 않고 상태만 정리
reset(scan=True); SECTIONS["naver"] = [("관심작가신작", "작가", 5)]; rd.note_download_finished(T0); tick(T0 + MIN(10))
assert sent == [] and SCAN["start"] == [] and state()["last_download_at"] is None
reset(); SECTIONS["kakao"] = [(777, "카카오신작", "5화")]; add_row("받은것", T0); rd.note_download_finished(T0); tick(T0 + MIN(10))
assert len(sent) == 1 and "받은것" in sent[0] and "카카오신작" in sent[0], sent
# 웹훅 지우기: set_webhook_url("")은 기존 값을 유지하는 규칙이라 설정 칸을 직접 비운다
reset(scan=True); repository.set_setting(discord_config._KEY_WEBHOOK_URL, None)
add_row("웹훅없음", T0); rd.note_download_finished(T0); tick(T0 + MIN(10))
# 리포트는 못 보내도(웹훅 없음) 새로 받은 게 있으면 뷰어는 새 파일을 훑어야 한다 — 스캔은 리포트/웹훅과 상관없이 요청한다
assert sent == [] and len(SCAN["start"]) == 1 and state()["last_download_at"] is None, (sent, SCAN, state())
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")
print("8) 이력 없음/웹훅 없음(스캔은 함) OK")

# Scenario 9: 다운로드 작업이 끝나면 자동으로 "다운로드가 끝났다"를 기록한다(download/manual만) — 이 작업이 시작한 시각을 발송 기준으로 삼는다(처음 한 번)
reset(); repository.set_setting(LAST_SENT, None); job_status.add_finish_listener(rd.on_job_finished)
job_status.start("archive"); job_status.finish("archive", True); assert state()["last_download_at"] is None, "아카이빙은 해당 없음"
job_status.start("download"); started = job_status.snapshot()["download"]["started_at"]; job_status.finish("download", True)
assert state()["last_download_at"] is not None and repository.get_setting(LAST_SENT) == started, (state(), started)                      # 발송 기준이 없으면 이 작업이 시작한 시각으로
job_status.start("manual"); job_status.finish("manual", False); assert state()["last_download_at"] is not None
print("9) 작업 종료 연동 OK")
# Scenario 10: 시작할 때 — 작업 종료 연결과 옛 "발송 시각" 스케줄 정리, 1분 확인 작업 등록, 스케줄 API에서 report_job이 사라졌다
import httpx
repository.set_setting("schedule_report_job", '{"mode": "cron", "cron_times": [{"hour": 9, "minute": 0}]}')
job_status._finish_listeners.clear(); rd.start()
assert repository.get_setting("schedule_report_job") is None and rd.on_job_finished in job_status._finish_listeners
rd.start(); assert job_status._finish_listeners.count(rd.on_job_finished) == 1                                              # 여러 번 불러도 하나
async def registration():
    s = scheduler.create_scheduler(); job = s.get_job("report_tick")
    assert job is not None and job.trigger.interval == MIN(1) and job.max_instances == 1 and "report_job" not in scheduler.DEFAULT_SCHEDULES
    import app.main as m
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        assert "report_job" not in (await c.get("/api/settings")).json()                                                    # 설정 화면의 스케줄 목록에 없다
        off = {"mode": "off", "interval_minutes": 60, "cron_times": [{"hour": 3, "minute": 0}], "target": "naver"}
        assert (await c.post("/api/settings", json={"discovery_job": off, "download_job": [], "archive_job": off})).status_code == 200      # report_job 없이 저장된다
asyncio.run(registration())
print("10) 시작 연결/1분 확인 작업/스케줄 API OK")
# Scenario 11: 뷰어 스캔은 공용 — 아카이빙이 먼저 요청해 시작한 스캔이 다운로드 묶음도 덮으면(그 스캔이 변경 뒤에 시작) 새로 시작하지 않고 그 스캔이 끝나기를 기다렸다 리포트를 보낸다.
# 실패만 있는 묶음은 스캔을 요청하지 않는다(새 파일이 없다). 할 일이 없는 확인은 서버에 아무 요청도 하지 않는다
reset(scan=True); tick(T0)
assert SCAN["start"] == [] and SCAN["poll"] == 0                                                                           # 대기 중인 것도 진행 중인 스캔도 없으면 조용하다
viewer_scan.request(T0, scope="fast"); tick(T0 + MIN(1)); assert len(SCAN["start"]) == 1                                     # 다른 곳이 요청해서 로컬 스캔 시작(T0+1)
add_row("먼저받음", T0); rd.note_download_finished(T0)                                                                       # 그 스캔이 시작되기 전에 받은 것
tick(T0 + MIN(10)); assert sent == [] and len(SCAN["start"]) == 1, (sent, SCAN)                                              # 묶음 확정: 이미 시작한 스캔이 덮으니 새로 시작하지 않고 끝나기를 기다린다
SCAN["running"] = False; tick(T0 + MIN(11)); assert len(sent) == 1 and "먼저받음" in sent[0] and len(SCAN["start"]) == 1
reset(scan=True); add_row("실패만", T0, status="failed"); rd.note_download_finished(T0); tick(T0 + MIN(10))
assert len(sent) == 1 and "실패만" in sent[0] and SCAN["start"] == [], (sent, SCAN)                                         # 스캔 없이 바로 리포트
reset(scan=True); job_status.add_finish_listener(rd.on_job_finished)                                                       # 수동 다운로드(manual 작업)가 끝난 경우도 같다 — 웹훅이 없어도 스캔은 한다
repository.set_setting(discord_config._KEY_WEBHOOK_URL, None); job_status.start("manual"); add_row("수동으로받음", datetime.now(UTC)); job_status.finish("manual", True)
tick(datetime.now(UTC) + MIN(10)); assert sent == [] and len(SCAN["start"]) == 1, (sent, SCAN)
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")
# 웹훅이 없으면 발송 기준 시각이 움직이지 않으니, 이미 스캔을 요청한 이력을 다음 묶음에서 또 따지면 안 된다(워터마크) — 두 번째 묶음에 실패만 있으면 스캔은 그대로 한 번
reset(scan=True); repository.set_setting(discord_config._KEY_WEBHOOK_URL, None)
add_row("첫묶음", T0); rd.note_download_finished(T0); tick(T0 + MIN(10)); assert len(SCAN["start"]) == 1
SCAN["running"] = False; tick(T0 + MIN(11))
add_row("둘째묶음실패", T0 + MIN(20), status="failed"); rd.note_download_finished(T0 + MIN(20)); tick(T0 + MIN(30)); assert len(SCAN["start"]) == 1, SCAN
add_row("셋째묶음", T0 + MIN(40)); rd.note_download_finished(T0 + MIN(40)); tick(T0 + MIN(50)); assert len(SCAN["start"]) == 2, SCAN             # 새로 받은 것이 있으면 다시
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")
print("11) 공용 스캔/실패만/수동 다운로드/워터마크 OK")
# Scenario 12: 상태 저장은 동시에 일어나도 서로 덮어쓰지 않는다 — 상태는 JSON 한 덩어리를 읽고-바꾸고-쓰는 방식이라, 다른 스레드에서 동시에 갱신하면(작업 종료 리스너 등) 보호하지 않으면 앞의 갱신이 사라진다
from concurrent.futures import ThreadPoolExecutor
repository.set_setting(rd.STATE_KEY, None)
with ThreadPoolExecutor(8) as pool: list(pool.map(lambda i: rd.update_state(**{f"k{i}": i}), range(120)))
assert all(f"k{i}" in rd.load_state() for i in range(120)), f"동시에 갱신하면 사라진다: {sum(1 for i in range(120) if f'k{i}' in rd.load_state())}/120"
repository.set_setting(rd.STATE_KEY, None)
print("12) 동시 갱신 OK")
print("ALL OK")
