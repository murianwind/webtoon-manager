"""웹툰 서버(뷰어)와 주고받은 일을 실행 이력("웹툰 서버 연동", job_name=viewer_server)에 남긴다.
① 스캔(새로고침): 스캔이 필요해진 때(사유: 다운로드 N건/아카이빙에서 N개 이동) → 서버에 스캔을 시킨 결과 → 끝남/건너뜀/추적 중단까지를 **한 번의 스캔 주기당 이력 한 건**으로(중간에 새 요청이 와도 같은 주기).
   주기가 진행 중일 때는 이력이 아직 없고(실시간 상태만), 끝나거나 포기하면 저장된다. 컨테이너가 다시 시작돼도(진행 상태는 저장돼 있다) 끝나면 이력이 남는다.
② 읽은 회차 조회(아카이빙이 뷰어에 읽음 상태를 물은 것): 실행마다 한 건 — 작품별 결과(읽은 회차 수/조회 실패 → 옮기지 않음).
③ 리포트의 바로가기 링크 조회: 리포트마다 한 건 — 성공/실패 수와 못 찾은 작품.
뷰어 연동이 꺼져 있거나 설정이 없으면 아무것도 남기지 않는다. 이전에는 스캔 진행 로그가 "다운로드 리포트" 로그에 섞여 들어갔다(별도 이력으로 남지 않았다)."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, job_status, repository, report_dispatch, viewer_scan, webtoon_server_client
db.get_connection()
from app.config import get_settings
from app.scheduler import archive_job as aj, report_job, report_tick

UTC = timezone.utc
T0 = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
MIN = lambda n: timedelta(minutes=n)
SCAN = {}
async def fake_start(session, base, token, timeout): return SCAN["start_result"]
async def fake_status(session, base, token, timeout): return SCAN["status"]
webtoon_server_client.start_scan, webtoon_server_client.scan_status = fake_start, fake_status

def reset(*, ready=True, max_wait=45):
    SCAN.update(start_result="started", status={"running": True})
    repository.clear_job_history(); job_status._statuses["viewer_server"] = job_status._JobStatus()
    repository.set_setting(viewer_scan.STATE_KEY, None)
    repository.set_setting("webtoon_server_url", "http://viewer" if ready else None); viewer_scan.set_config(enabled=ready, token="secret-token" if ready else "", max_wait_minutes=max_wait)
tick = lambda now: asyncio.run(viewer_scan.tick(None, now, 5))
entries = lambda: [e for e in repository.list_job_history(50) if e["job_name"] == "viewer_server"]
live = lambda: job_status.snapshot()["viewer_server"]
text = lambda e: "\n".join(e["log"])
live_text = lambda: "\n".join(live()["log"])

# Scenario 1: 한 번의 스캔 주기 = 이력 한 건 — 요청 → 시작 → (진행 중에는 이력 없음, 상태 확인은 줄을 남기지 않는다) → 끝남. 사유와 소요 시간이 들어 있다
reset(); viewer_scan.request(T0, reason="다운로드 3건 완료")
assert entries() == [] and live()["status"] == "running" and "스캔 필요: 다운로드 3건 완료" in live_text(), live()
tick(T0 + MIN(1)); assert "웹툰 서버에 스캔을 시작시켰습니다" in live_text() and entries() == []
before = len(live()["log"]); tick(T0 + MIN(2)); tick(T0 + MIN(3)); assert len(live()["log"]) == before and entries() == [], "진행 확인은 줄을 늘리지 않는다"
SCAN["status"] = {"running": False}; tick(T0 + MIN(5))
(e,) = entries(); assert e["status"] == "success" and live()["status"] == "success", (e, live())
assert [l.split(" — ", 1)[1] for l in e["log"]] == ["스캔 필요: 다운로드 3건 완료", "웹툰 서버에 스캔을 시작시켰습니다", "스캔이 끝났습니다 (시작 후 4분)"], e["log"]
tick(T0 + MIN(6)); tick(T0 + MIN(7)); assert len(entries()) == 1, "끝난 뒤에는 이력이 더 늘지 않는다"
print("1) 스캔 한 주기 = 이력 한 건 OK")

# Scenario 2: 스캔 도중 새 변경이 생기면 같은 주기에 이어진다 — 이전 스캔이 끝나면 다시 시작하고, 두 번째가 끝나야 이력이 저장된다(한 건). 이미 덮인 요청은 줄을 남기지 않는다
reset(); viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1))
viewer_scan.request(T0 + MIN(1), reason="덮인 요청"); assert "덮인 요청" not in live_text(), "이미 시작한 스캔이 덮는 요청은 기록하지 않는다"
viewer_scan.note_library_changed(2, T0 + MIN(3), source="정기 아카이빙")
SCAN["status"] = {"running": False}; tick(T0 + MIN(5))                                                                  # 첫 스캔 끝 → 곧바로 두 번째 시작
assert entries() == [] and live()["status"] == "running", "두 번째 스캔이 도는 중이라 주기는 계속된다"
tick(T0 + MIN(6)); (e,) = entries(); assert e["status"] == "success"
assert [l.split(" — ", 1)[1] for l in e["log"]] == ["스캔 필요: 다운로드 1건 완료", "웹툰 서버에 스캔을 시작시켰습니다", "스캔 필요: 정기 아카이빙에서 파일 2개를 옮김", "스캔이 끝났습니다 (시작 후 4분)",
                                                  "웹툰 서버에 스캔을 시작시켰습니다", "스캔이 끝났습니다 (시작 후 1분)"], e["log"]
print("2) 스캔 도중 새 변경은 같은 주기 OK")

# Scenario 3: 뷰어가 이미 스캔 중이면 끝나기를 기다렸다가 시작한다 — 그 사실이 남는다
reset(); SCAN["start_result"] = "running"; viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1))
assert "웹툰 서버가 이미 스캔 중이라 끝난 뒤 시작합니다" in live_text() and entries() == []
SCAN["status"] = {"running": False}; SCAN["start_result"] = "started"; tick(T0 + MIN(4)); SCAN["status"] = {"running": False}; tick(T0 + MIN(6))
(e,) = entries(); assert e["status"] == "success" and "이미 스캔 중" in text(e) and "스캔이 끝났습니다" in text(e), e["log"]
print("3) 이미 스캔 중이면 기다림 OK")

# Scenario 4: 연동을 쓸 수 없거나(404/401/403)/연결 오류면 건너뛰고 — 오류 이력 한 건(왜 건너뛰었는지)
for result, word in (("unsupported", "연동을 쓸 수 없어"), ("error", "연결하지 못해")):
    reset(); SCAN["start_result"] = result; viewer_scan.request(T0, reason="다운로드 2건 완료"); tick(T0 + MIN(1))
    (e,) = entries(); assert e["status"] == "error" and word in text(e) and "건너뜁니다" in text(e) and "스캔 필요: 다운로드 2건 완료" in text(e), (result, e["log"])
    tick(T0 + MIN(2)); assert len(entries()) == 1
# 실패한 주기의 "실패" 표시는 그 주기에서 끝난다 — 다음 주기가 정상이면 성공으로 남는다
reset(); SCAN["start_result"] = "error"; viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1))
SCAN["start_result"] = "started"; viewer_scan.request(T0 + MIN(10), reason="다운로드 2건 완료"); tick(T0 + MIN(11)); SCAN["status"] = {"running": False}; tick(T0 + MIN(13))
statuses = [e["status"] for e in sorted(entries(), key=lambda e: e["id"])]; assert statuses == ["error", "success"], statuses
print("4) 연동 없음/연결 오류 OK")

# Scenario 5: 너무 오래 걸리면(최대 대기) 추적을 멈추고 오류 이력 한 건 — 우리가 시작한 스캔, 남이 돌리는 스캔을 기다리던 경우 모두
reset(max_wait=45); viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1)); tick(T0 + MIN(46))
(e,) = entries(); assert e["status"] == "error" and "45분 안에 끝나지 않아 더 이상 추적하지 않습니다" in text(e), e["log"]
reset(max_wait=45); SCAN["start_result"] = "running"; viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1)); tick(T0 + MIN(46))
(e,) = entries(); assert e["status"] == "error" and "45분 안에 끝나지 않아 스캔 요청을 건너뜁니다" in text(e), e["log"]
print("5) 오래 걸리면 추적 중단 OK")

# Scenario 6: 연동이 꺼져 있거나 설정이 없으면 아무것도 남기지 않는다(실시간 상태도 안 바뀐다)
reset(ready=False); viewer_scan.request(T0, reason="다운로드 1건 완료"); viewer_scan.note_library_changed(3, T0); tick(T0 + MIN(1))
assert entries() == [] and live()["status"] == "idle" and live()["log"] == []
print("6) 연동 없으면 기록 없음 OK")

# Scenario 7: 사유 — 파일을 옮겼다고 알리는 쪽은 출처를 적는다(기본 "아카이빙"), 0개면 아무것도 안 한다
reset(); viewer_scan.note_library_changed(3, T0); assert "스캔 필요: 아카이빙에서 파일 3개를 옮김" in live_text()
reset(); viewer_scan.note_library_changed(0, T0, source="폴더 일괄 이동"); assert live()["status"] == "idle"
reset(); viewer_scan.note_library_changed(7, T0, source="폴더 일괄 이동"); assert "스캔 필요: 폴더 일괄 이동에서 파일 7개를 옮김" in live_text()
reset(); viewer_scan.request(T0); assert "스캔 필요: 새 파일" in live_text(), "사유를 안 주면 기본 문구"
print("7) 사유 OK")

# Scenario 8: 다운로드 묶음이 스캔을 요청할 때의 사유 — 그 묶음에서 성공한 다운로드 수
reset(); repository.set_setting(report_dispatch.LAST_SENT_KEY, (T0 - MIN(60)).isoformat())
rows = [{"status": "success"}, {"status": "failed"}, {"status": "success"}]
real_window = repository.list_episode_history_window; repository.list_episode_history_window = lambda since, until: rows
report_tick._request_scan_for_batch(T0.isoformat()); repository.list_episode_history_window = real_window
assert "스캔 필요: 다운로드 2건 완료" in live_text(), live_text()
print("8) 다운로드 묶음 사유 OK")

# Scenario 9: 컨테이너가 다시 시작돼 실시간 상태가 사라져도(진행 상태는 저장돼 있다) 스캔이 끝나면 이력이 남는다
reset(); viewer_scan.request(T0, reason="다운로드 1건 완료"); tick(T0 + MIN(1))
job_status._statuses["viewer_server"] = job_status._JobStatus()                                                       # 재시작 흉내
SCAN["status"] = {"running": False}; tick(T0 + MIN(8))
(e,) = entries(); assert e["status"] == "success" and "스캔이 끝났습니다 (시작 후 7분)" in text(e), e["log"]
print("9) 재시작 뒤에도 이력 OK")

# ── ② 아카이빙의 읽은 회차 조회 ──
READ = {}
async def fake_read(session, base, folder_name, timeout): return READ.get(folder_name)
webtoon_server_client.fetch_read_numbers = fake_read
settings = get_settings()
async def lookups(source, calls, **kw):
    async with aj.read_numbers_provider_for(settings, source=source) as provider:
        if provider is None: return None
        await asyncio.to_thread(lambda: [provider(title, kakao) for title, kakao in calls])
        return provider

# Scenario 10: 조회한 작품별 결과 한 건 — 성공(읽은 회차 수, 0개 포함)과 실패(옮기지 않음, 카카오 표시). 하나라도 실패하면 오류
reset(); READ.update({"작품A": frozenset({1, 2, 3}), "작품C": frozenset()})
asyncio.run(lookups("정기 아카이빙", [("작품A", False), ("작품B", True), ("작품C", False)]))
(e,) = entries(); assert e["status"] == "error", e
assert [l.split(" — ", 1)[1] for l in e["log"]] == ["정기 아카이빙 — 웹툰 서버에 읽은 회차를 조회했습니다 (3개 작품: 성공 2, 실패 1)", "- 작품A: 읽은 회차 3개",
                                                  "- [카카오] 작품B: 조회 실패 → 옮기지 않음", "- 작품C: 읽은 회차 0개"], e["log"]
reset(); READ.update({"작품A": frozenset({1}), "작품B": frozenset({2, 3})})
asyncio.run(lookups("지금 이동(수동)", [("작품A", False), ("작품B", False)])); (e,) = entries(); assert e["status"] == "success" and "지금 이동(수동) — 웹툰 서버에" in e["log"][0] and "실패 0" in e["log"][0]
print("10) 읽은 회차 조회 이력 OK")

# Scenario 11: 조회가 없었으면(뷰어 주소 없음/조회할 작품 없음) 남기지 않는다. 너무 많으면 앞의 30개 작품만 풀고 나머지는 개수만. 도중에 오류가 나도 지금까지의 조회는 남긴다
reset(ready=False); assert asyncio.run(lookups("정기 아카이빙", [("작품A", False)])) is None and entries() == []
reset(); asyncio.run(lookups("정기 아카이빙", [])); assert entries() == []
reset(); READ.update({f"작품{i}": frozenset({i}) for i in range(40)}); asyncio.run(lookups("정기 아카이빙", [(f"작품{i}", False) for i in range(40)]))
(e,) = entries(); assert len(e["log"]) == 1 + 30 + 1 and "(40개 작품: 성공 40, 실패 0)" in e["log"][0] and e["log"][-1].endswith("… 외 10개 작품"), (len(e["log"]), e["log"][-1])
reset(); READ.update({"작품A": frozenset({1})})
async def boom():
    async with aj.read_numbers_provider_for(settings, source="정기 아카이빙") as provider:
        await asyncio.to_thread(lambda: provider("작품A", False)); raise RuntimeError("아카이빙 중 오류")
try: asyncio.run(boom())
except RuntimeError: pass
(e,) = entries(); assert "성공 1" in e["log"][0]
print("11) 조회 없음/많음/오류 OK")

# ── ③ 리포트 바로가기 링크 조회 ──
URLS = {}
async def fake_reader(session, base, folder_name, timeout): return URLS.get(folder_name)
webtoon_server_client.fetch_reader_url = fake_reader
resolve = lambda rows: asyncio.run(report_job._resolve_reader_urls(None, settings, rows))
# Scenario 12: 리포트마다 한 건 — 성공/실패 수와 못 찾은 작품(카카오 표시). 전부 찾으면 성공, 못 찾은 게 있으면 오류. 조회할 게 없으면 남기지 않는다
reset(); URLS.update({"가": "http://viewer/가", "다": "http://viewer/다"})
rows = [{"title_name": "가", "platform": "naver"}, {"title_name": "나", "platform": "naver"}, {"title_name": "다", "platform": "kakao"}]
urls = resolve(rows); assert urls == {"가": "http://viewer/가"} or "가" in urls, urls
(e,) = entries(); assert e["status"] == "error" and e["log"][0].split(" — ", 1)[1] == "다운로드 리포트 — 웹툰 서버에서 바로가기 링크를 조회했습니다 (3개 작품: 성공 2, 실패 1)", e["log"]
assert e["log"][1].split(" — ", 1)[1] == "- 나: 링크를 찾지 못함", e["log"]
reset(); URLS.update({"가": "http://viewer/가"}); resolve([{"title_name": "가", "platform": "naver"}]); (e,) = entries(); assert e["status"] == "success" and "성공 1, 실패 0" in e["log"][0]
reset(); resolve([]); assert entries() == []
reset(ready=False); resolve(rows); assert entries() == [], "뷰어 주소가 없으면 조회도 기록도 없다"
print("12) 리포트 링크 조회 이력 OK")
print("ALL OK")
