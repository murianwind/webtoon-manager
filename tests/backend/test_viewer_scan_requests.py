"""뷰어 서버 스캔을 시키는 공용 조정 장치(viewer_scan) — 다운로드(수동 다운로드 포함)와 아카이빙(자동/수동/일괄 이동)이 새 파일을 만들면 뷰어가 다시 훑게 한다.

핵심 약속: "변경 시각 이후에 시작한 스캔이 끝나야 그 변경이 반영된다."
- request(when): 스캔이 필요한 변경을 기록한다(뷰어 연동이 켜져 있고 준비됐을 때만). 같은 변경에 스캔을 두 번 시키지 않는다.
- tick(): 1분마다 한 번 — 우리가 시작한 스캔이 끝났는지만 확인하거나(서버에는 가벼운 상태 조회), 필요하면 새로 시작한다.
  스캔 도중에 새 변경이 생기면 그 스캔이 끝난 뒤 다시 시작한다. 뷰어가 이미 스캔 중이면(자동 재스캔 등) 끝나기를 기다렸다가 시작한다.
- 연동이 없거나(404)/토큰이 안 맞거나(401·403)/연결 오류면 그 요청은 버리고(재시도 폭주 없음), 너무 오래 걸리면 추적을 멈춘다.
"""
import asyncio
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db, repository, scheduler, viewer_scan, webtoon_server_client
from app.config import get_settings
db.get_connection()
import app.main as m

UTC = timezone.utc
T0 = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
MIN = lambda n: timedelta(minutes=n)
SCAN = {}
async def fake_start(session, base, token, timeout): SCAN["start"].append((base, token)); return SCAN["start_result"]
async def fake_status(session, base, token, timeout): SCAN["poll"] += 1; return SCAN["status"]
webtoon_server_client.start_scan = fake_start; webtoon_server_client.scan_status = fake_status
logs = []
viewer_scan.job_status.log_line = lambda job, line: logs.append(line)


def reset(*, ready=True, max_wait=45):
    SCAN.update(start=[], poll=0, start_result="started", status={"running": True}); logs.clear()
    repository.set_setting(viewer_scan.STATE_KEY, None)
    repository.set_setting("webtoon_server_url", "http://viewer" if ready else None); viewer_scan.set_config(enabled=ready, token="secret-token" if ready else "", max_wait_minutes=max_wait)
def tick(now): asyncio.run(viewer_scan.tick(None, now, 5))
def state(): return viewer_scan.load_state()

# Scenario 1: 준비되지 않았으면(꺼짐/토큰 없음/주소 없음) 요청은 기록하지 않고 할 일도 없다 — 서버에 아무 요청도 안 간다
for kw in ({"ready": False},):
    reset(**kw); viewer_scan.request(T0); assert not viewer_scan.has_work() and state()["requested_at"] is None; tick(T0 + MIN(1)); assert SCAN["start"] == [] and SCAN["poll"] == 0
reset(); repository.set_setting("webtoon_server_url", None); viewer_scan.request(T0); assert not viewer_scan.has_work()                    # 주소가 없으면
reset(); viewer_scan.set_config(enabled=True, token=""); viewer_scan.request(T0); assert not viewer_scan.has_work()                        # 토큰을 지우면
reset(); viewer_scan.set_config(enabled=False); viewer_scan.request(T0); assert not viewer_scan.has_work()                                 # 꺼 두면
print("1) 준비 안 됨 OK")

# Scenario 2: 변경 요청 → 한 번 시작(주소와 토큰으로) → 이후엔 진행 상태만 확인 → 끝나면 더 이상 아무것도 안 한다
reset(); viewer_scan.request(T0)
assert viewer_scan.has_work() and viewer_scan.covered_state(T0) == "waiting"
tick(T0 + MIN(1)); assert SCAN["start"] == [("http://viewer", "secret-token")] and SCAN["poll"] == 0 and viewer_scan.covered_state(T0) == "waiting" and state()["requested_at"] is None
tick(T0 + MIN(2)); tick(T0 + MIN(3)); assert len(SCAN["start"]) == 1 and SCAN["poll"] == 2                                                   # 시작은 한 번, 진행 확인만
SCAN["status"] = {"running": False}; tick(T0 + MIN(4)); assert viewer_scan.covered_state(T0) == "done" and not viewer_scan.has_work()
tick(T0 + MIN(5)); assert SCAN["poll"] == 3 and len(SCAN["start"]) == 1                                                                     # 끝난 뒤에는 요청도 확인도 없다
print("2) 시작/진행 확인/완료 OK")

# Scenario 3: 같은 변경에 스캔을 두 번 시키지 않는다 — 이미 시작한 스캔보다 먼저 일어난 변경(또는 같은 시각)의 요청은 그 스캔이 덮는다. 더 늦은 변경은 끝난 뒤 다시
reset(); viewer_scan.request(T0); tick(T0 + MIN(1))                                                                                          # 시작 시각 T0+1분
viewer_scan.request(T0 + MIN(1)); assert viewer_scan.covered_state(T0 + MIN(1)) == "waiting"                                                  # 시작과 같은 시각 변경: 그 스캔이 덮는다
tick(T0 + MIN(2)); assert len(SCAN["start"]) == 1
viewer_scan.request(T0 + MIN(3))                                                                                                             # 스캔이 도는 중에 새 변경(시작 뒤)
assert viewer_scan.covered_state(T0 + MIN(3)) == "waiting"
tick(T0 + MIN(4)); assert len(SCAN["start"]) == 1 and SCAN["poll"] == 2                                                                      # 진행 중에는 새로 시작하지 않는다
SCAN["status"] = {"running": False}; tick(T0 + MIN(5)); assert len(SCAN["start"]) == 2                                                         # 끝나는 즉시(같은 확인에서) 다시 시작
assert viewer_scan.covered_state(T0 + MIN(3)) == "waiting" and viewer_scan.covered_state(T0) == "done"
SCAN["status"] = {"running": False}; tick(T0 + MIN(6)); assert viewer_scan.covered_state(T0 + MIN(3)) == "done" and len(SCAN["start"]) == 2
print("3) 중복 방지/스캔 중 새 변경 OK")

# Scenario 4: 뷰어가 이미 스캔 중이면(자동 재스캔 등 우리가 시작하지 않은 스캔) 끝나기를 기다렸다가 시작한다 — 그 스캔은 우리 변경 전에 시작했을 수 있어서 그것만으로는 반영을 장담할 수 없다
reset(); SCAN["start_result"] = "running"; viewer_scan.request(T0); tick(T0 + MIN(1))
assert len(SCAN["start"]) == 1 and state()["foreign_wait"] is True and viewer_scan.covered_state(T0) == "waiting"
tick(T0 + MIN(2)); tick(T0 + MIN(3)); assert len(SCAN["start"]) == 1 and SCAN["poll"] == 2                                                    # 끝날 때까지 상태만 확인
SCAN["status"] = {"running": False}; SCAN["start_result"] = "started"; tick(T0 + MIN(4))
assert len(SCAN["start"]) == 2 and state()["foreign_wait"] is False and state()["started_at"] == (T0 + MIN(4)).isoformat()                    # 끝나자마자 우리 스캔 시작
print("4) 이미 스캔 중이면 기다렸다 시작 OK")

# Scenario 5: 연동이 없거나(unsupported)/연결 오류면 요청을 버린다 — 다시 시도하지 않고, "실패"로 알려서 리포트가 기다리지 않게 한다
for result in ("unsupported", "error"):
    reset(); SCAN["start_result"] = result; viewer_scan.request(T0); tick(T0 + MIN(1))
    assert state()["requested_at"] is None and not viewer_scan.has_work() and viewer_scan.covered_state(T0) == "failed" and len(SCAN["start"]) == 1 and any("건너뜁니다" in l for l in logs)
    tick(T0 + MIN(2)); tick(T0 + MIN(3)); assert len(SCAN["start"]) == 1                                                                      # 재시도 폭주 없음
print("5) 연동 없음/오류는 버림 OK")

# Scenario 6: 너무 오래 걸리면 추적을 멈춘다(최대 대기 45분) — 우리가 시작한 스캔을 계속 확인하지 않고, 남이 돌리는 스캔이 안 끝나면 요청을 버린다
reset(max_wait=45); viewer_scan.request(T0); tick(T0 + MIN(1))
tick(T0 + MIN(30)); assert SCAN["poll"] == 1 and viewer_scan.has_work()
tick(T0 + MIN(46)); assert not viewer_scan.has_work() and SCAN["poll"] == 1 and any("추적" in l for l in logs), (SCAN, logs)                    # 시작(1분) + 45분 뒤 확인 없이 추적 중단
reset(max_wait=45); SCAN["start_result"] = "running"; viewer_scan.request(T0); tick(T0 + MIN(1)); tick(T0 + MIN(30)); assert viewer_scan.has_work()
tick(T0 + MIN(46)); assert not viewer_scan.has_work() and state()["requested_at"] is None and state()["foreign_wait"] is False and viewer_scan.covered_state(T0) == "failed"
print("6) 오래 걸리면 추적 중단 OK")

# Scenario 7: 변경이 생겼다고 알리는 곳 — 새 파일이 0개면 아무것도 안 하고, 1개 이상이면 요청한다
reset(); viewer_scan.note_library_changed(0, T0); assert state()["requested_at"] is None
viewer_scan.note_library_changed(3, T0); assert state()["requested_at"] == T0.isoformat()
reset(ready=False); viewer_scan.note_library_changed(3, T0); assert state()["requested_at"] is None
print("7) 변경 알림 OK")

# Scenario 8: 아카이빙 — 자동(정기+완결 대기열) 아카이빙 잡이 파일을 옮기면 스캔을 요청하고, 하나도 안 옮겼으면 하지 않는다
ST = get_settings(); Path(ST.archive_root).mkdir(parents=True, exist_ok=True)
async def noop_update(): return "rclone 확인 생략"
scheduler.archive_job.rclone_updater.check_and_update = noop_update
def run_auto(periodic, pending):
    scheduler.archive_job.archiver.run_periodic_archive = lambda *a, **k: periodic
    scheduler.archive_job.archiver.process_pending_finish_archives = lambda *a, **k: pending
    asyncio.run(scheduler.run_archive_job())
reset(); run_auto(0, 0); assert state()["requested_at"] is None
reset(); run_auto(2, 0); assert state()["requested_at"] is not None
reset(); run_auto(0, 3); assert state()["requested_at"] is not None                                                                           # 완결 대기열로 옮긴 것도
print("8) 자동 아카이빙 OK")

# Scenario 9: 수동 아카이빙(수동 실행 탭)과 폴더 일괄 이동도 같다
async def api():
    import app.api.archive as api_archive
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        api_archive.rclone_updater.check_and_update = noop_update
        async def wait_idle(): await asyncio.sleep(0.2)
        def fake_manual(moved): return lambda *a, **k: moved
        # 선택한 웹툰 수동 아카이빙
        reset(); api_archive.archiver.manual_archive_now = fake_manual(0); await c.post("/api/archive/run", json={"title_ids": ["1"], "full_move": True}); await wait_idle(); assert state()["requested_at"] is None
        reset(); api_archive.archiver.manual_archive_now = fake_manual(4); await c.post("/api/archive/run", json={"title_ids": ["1"], "full_move": True}); await wait_idle(); assert state()["requested_at"] is not None
        # 전체 수동 실행(지정 웹툰 + 완결 대기열)
        api_archive.archiver.process_pending_finish_archives = lambda *a, **k: 5
        reset(); api_archive.archiver.manual_archive_now = fake_manual(0); await c.post("/api/archive/run", json={}); await wait_idle(); assert state()["requested_at"] is not None
        api_archive.archiver.process_pending_finish_archives = lambda *a, **k: 0
        reset(); await c.post("/api/archive/run", json={}); await wait_idle(); assert state()["requested_at"] is None
        # 폴더 일괄 이동
        body = {"source_type": "local", "source_path": "a", "source_local_root": "archive", "dest_type": "local", "dest_path": "b", "dest_local_root": "archive"}
        reset(); api_archive.archiver.bulk_move_folder = lambda *a, **k: 0; await c.post("/api/archive/bulk-move", json=body); await wait_idle(); assert state()["requested_at"] is None
        reset(); api_archive.archiver.bulk_move_folder = lambda *a, **k: 7; await c.post("/api/archive/bulk-move", json=body); await wait_idle(); assert state()["requested_at"] is not None
asyncio.run(api())
print("9) 수동 아카이빙/일괄 이동 OK")
# Scenario 10: 상태 저장은 동시에 일어나도 서로 덮어쓰지 않는다(JSON 한 덩어리를 읽고-바꾸고-쓰기) — 아카이빙이 끝났다는 알림과 1분 확인이 서로 다른 스레드에서 겹쳐도 요청이 사라지지 않아야 한다
from concurrent.futures import ThreadPoolExecutor
reset()
with ThreadPoolExecutor(8) as pool: list(pool.map(lambda i: viewer_scan.update_state(**{f"k{i}": i}), range(120)))
assert all(f"k{i}" in state() for i in range(120)), f"동시에 갱신하면 사라진다: {sum(1 for i in range(120) if f'k{i}' in state())}/120"
reset()
print("10) 동시 갱신 OK")
print("ALL OK")
