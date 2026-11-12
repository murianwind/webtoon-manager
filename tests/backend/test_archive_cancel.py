"""실행 중인 아카이빙 중단 — 중단을 요청하면 지금 옮기는 파일은 끝까지 옮긴 뒤 남은 파일과 남은 작품은 옮기지 않고 멈춘다(정보 파일 복사도 건너뜀).
정기 실행, 수동 "지금 이동", 완결 구독해제 대기열, 폴더 대상 모두 같은 요청을 본다. 새 실행은 이전의 중단 요청을 이어받지 않는다(실행을 시작할 때 비운다).
API: POST /api/archive/cancel — 실행 중일 때만 요청을 받는다."""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS

import httpx

from app import db, job_status, rclone_updater, repository, archiver
from app.archiver import cancel
from app.archiver.params import ProgressReport, TitleArchiveJob
from app.config import get_settings
db.get_connection()
import app.main as m

async def no_update(*args, **kwargs): return "rclone 업데이트 확인 생략(테스트)"
rclone_updater.check_and_update = no_update
st = get_settings()
A = Path(st.archive_root); D = Path(st.download_root); A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)

def make(title, nums, extra=()):
    d = D / title; d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*"): p.unlink()
    for n in nums: (d / f"{n:04d} {n}화.zip").write_bytes(b"zip")
    for name in extra: (d / name).write_text("<x/>")
    return d
left = lambda d: sorted(p.name for p in Path(d).glob("*.zip"))
def after_first_move(progress_log):
    """진행 메시지에서 첫 "이동 완료"가 나오면 중단을 요청하는 콜백(실행 도중에 사용자가 중단을 누른 상황)."""
    def callback(message):
        progress_log.append(message)
        if "이동 완료" in message: cancel.request()
    return callback
def title_job(title, base, **kw): return TitleArchiveJob(archive_root=st.archive_root, download_root=st.download_root, title_id=kw.pop("title_id", "921"), title_name=title, base_path=base, policy="skip", trigger_type="periodic", keep_last=kw.pop("keep_last", True), **kw)

# Scenario 1: 요청 상태 — 처음엔 요청 없음, request()하면 요청됨, reset()하면 풀림
cancel.reset(); assert not cancel.is_requested(); cancel.request(); assert cancel.is_requested(); cancel.reset(); assert not cancel.is_requested()
print("1) 요청 상태 OK")

# Scenario 2: 한 작품 이동 중 중단 — 첫 파일(진행 중이던 파일)은 끝까지 옮기고 나머지는 옮기지 않으며, 남긴 개수를 알린다. 정보 파일(info.xml)도 복사하지 않는다
cancel.reset(); log = []
src = make("중단작A", [1, 2, 3, 4, 5], extra=["info.xml"])
moved = archiver._archive_title(title_job("중단작A", "보관A"), ProgressReport(after_first_move(log)))
assert moved == 1 and left(src) == ["0002 2화.zip", "0003 3화.zip", "0004 4화.zip", "0005 5화.zip"] and left(A / "보관A") == ["0001 1화.zip"], (moved, left(src))
assert any("중단 요청으로 남은 3개 파일은 옮기지 않았습니다" in m_ for m_ in log), log
assert not (A / "보관A" / "info.xml").exists(), "중단했으면 정보 파일도 복사하지 않는다"
print("2) 작품 이동 중 중단 OK")

# Scenario 3: 이미 중단이 요청된 상태에서 시작하면 하나도 옮기지 않는다(중단 요청이 남아 있는 채 호출된 경우)
src = make("중단작B", [1, 2, 3]); log = []
moved = archiver._archive_title(title_job("중단작B", "보관B"), ProgressReport(log.append))
assert moved == 0 and left(src) == ["0001 1화.zip", "0002 2화.zip", "0003 3화.zip"], left(src)
cancel.reset()
print("3) 요청된 채로 시작 OK")

# Scenario 4: 여러 작품 — 첫 작품 도중 중단하면 다음 작품은 시작하지도 않는다(수동 실행과 정기 실행 모두). 중단 문구는 한 번
for tid, title in (("931", "여럿1"), ("932", "여럿2")):
    repository.upsert_new(title_id=tid, title=title, added_source=repository.SOURCE_MANUAL); repository.upsert_archive_target(tid, f"보관{tid}", enabled=True)
for runner_name in ("수동", "정기"):
    cancel.reset(); log = []
    s1 = make("여럿1", [1, 2, 3, 4]); s2 = make("여럿2", [1, 2, 3, 4])
    for tid in ("931", "932"): repository.upsert_archive_target(tid, f"보관{tid}-{runner_name}", enabled=True)
    callback = after_first_move(log); asked_titles = []
    def provider(title_name, kakao): asked_titles.append(title_name); return frozenset({1, 2, 3, 4})
    if runner_name == "수동": moved = archiver.manual_archive_now(st.archive_root, st.download_root, ["931", "932"], progress_callback=callback, read_numbers_provider=provider)
    else: moved = archiver.run_periodic_archive(st.archive_root, st.download_root, progress_callback=callback, read_numbers_provider=provider)
    untouched = [d for d in (s1, s2) if len(left(d)) == 4]; started = [d for d in (s1, s2) if len(left(d)) == 3]            # 어느 작품이 먼저 처리되는지는 순서에 달렸다 — 하나만 시작됐고 하나는 시작도 안 했어야 한다
    assert moved == 1 and len(untouched) == 1 and len(started) == 1, (runner_name, moved, left(s1), left(s2))
    assert sum("이동 완료" in x for x in log) == 1, (runner_name, log)
    assert len(asked_titles) == 1, f"시작도 안 한 작품은 읽음 상태를 묻지 않는다: {asked_titles}"
    untouched_tid = "931" if untouched[0] is s1 else "932"
    assert not list(A.glob(f"보관{untouched_tid}-{runner_name}*")), "시작도 안 한 작품의 목적지 폴더는 만들지 않는다"
cancel.reset()
print("4) 여러 작품 중단 OK (수동/정기)")

# Scenario 5: 완결 구독해제 대기열 — 중단이 요청돼 있으면 대기열을 건드리지 않는다(다음 실행에서 이어서 한다)
repository.upsert_new(title_id="941", title="대기열작", added_source=repository.SOURCE_MANUAL); repository.add_pending_finish_archive("941")
archiver.title_archive.is_finish_unsubscribe_archiving_enabled = lambda: True; archiver.title_archive.get_default_base_path = lambda: "기본보관"; archiver.title_archive.get_default_dest_type = lambda: "local"
src = make("대기열작", [1, 2]); cancel.request()
assert archiver.process_pending_finish_archives(st.archive_root, st.download_root) == 0 and "941" in repository.list_pending_finish_archive() and left(src) == ["0001 1화.zip", "0002 2화.zip"]
cancel.reset()
print("5) 완결 대기열 OK")

# Scenario 6: 폴더 대상 — 파일 하나를 옮긴 뒤 중단하면 나머지는 옮기지 않는다
def mk_folder(rel, names):
    d = A / rel
    if d.exists():
        for p in sorted(d.rglob("*"), reverse=True): p.unlink() if p.is_file() else p.rmdir()
    d.mkdir(parents=True, exist_ok=True)
    for n in names: (d / n).write_bytes(b"x")
    return d
folder_target = NS(display_name="", source_dest_type="local", source_path="폴더원본", dest_type="local", dest_base_path="폴더대상", filename_template_preset_id=None, title_id="folder_cancel")
fsrc = mk_folder("폴더원본", ["0001_1화.zip", "0002_2화.zip", "0003_3화.zip", "0004_4화.zip", "info.xml"]); cancel.reset(); flog = []
archiver.title_archive  # (import 확인)
moved = archiver._archive_folder_target(folder_target, "skip", "", st.archive_root, True, after_first_move(flog), [], [])
assert moved == 1 and sorted(p.name for p in fsrc.glob("*.zip")) == ["0002_2화.zip", "0003_3화.zip", "0004_4화.zip"], (moved, sorted(p.name for p in fsrc.glob("*")))
assert any("중단 요청" in x for x in flog), flog
assert not (A / "폴더대상" / "info.xml").exists(), "중단했으면 정보 파일도 복사하지 않는다"
cancel.reset()
print("6) 폴더 대상 중단 OK")

# Scenario 7: API — 실행 중이 아니면 요청을 받지 않고(idle), 실행 중이면 요청하고 로그에 남긴다
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        cancel.reset(); job_status.start("archive"); job_status.finish("archive", success=True)
        r = await c.post("/api/archive/cancel"); assert r.json() == {"status": "idle"} and not cancel.is_requested(), r.text
        job_status.start("archive")
        r = await c.post("/api/archive/cancel"); assert r.json() == {"status": "requested"} and cancel.is_requested(), r.text
        assert "중단 요청을 받았습니다" in "\n".join(job_status.snapshot()["archive"]["log"])
        job_status.finish("archive", success=True); cancel.reset()
        # Scenario 8: "지금 이동"은 시작할 때 이전의 중단 요청을 비운다(남은 요청 때문에 새 실행이 아무것도 못 하면 안 된다). 중단으로 끝나면 로그에 그렇다고 남는다
        src = make("API중단작", [1, 2, 3, 4]); repository.upsert_new(title_id="951", title="API중단작", added_source=repository.SOURCE_MANUAL); repository.upsert_archive_target("951", "보관951", enabled=True)
        cancel.request()
        job_status.start("archive"); job_status.finish("archive", success=True)
        real_log = job_status.log_line
        def hooked(name, message):
            real_log(name, message)
            if name == "archive" and "이동 완료" in message: cancel.request()      # 실행 도중 중단 버튼을 누른 상황
        job_status.log_line = hooked
        try:
            await c.post("/api/archive/run", json={"title_ids": ["951"]})
            for _ in range(400):
                await asyncio.sleep(0.05)
                if job_status.snapshot()["archive"]["status"] != "running": break
            else: raise AssertionError("20초 안에 끝나지 않았다")
        finally: job_status.log_line = real_log
        log = "\n".join(job_status.snapshot()["archive"]["log"])
        assert left(src) == ["0002 2화.zip", "0003 2화.zip".replace("2화", "3화"), "0004 4화.zip"] and job_status.snapshot()["archive"]["status"] == "success", (left(src), log)
        assert "사용자 요청으로 중단했습니다" in log, log
asyncio.run(api())
print("7~8) 중단 API/실행 시작 시 비움 OK")

# Scenario 9: 정기 작업(run_archive_job)도 같다 — 시작할 때 이전 요청을 비우고, 실행 도중 중단하면 로그에 남기고 남은 파일은 그대로
from app.scheduler import archive_job
async def periodic_job():
    repository.upsert_new(title_id="961", title="정기중단작", added_source=repository.SOURCE_MANUAL); repository.upsert_archive_target("961", "보관961", enabled=True)
    for tid in ("931", "932", "951"): repository.upsert_archive_target(tid, f"보관{tid}", enabled=False)        # 이 시나리오에서는 대상 하나만
    src = make("정기중단작", [1, 2, 3, 4]); repository.set_setting("webtoon_server_url", "")
    cancel.request()                                                                                            # 남아 있던 요청 — 새 실행이 비워야 한다
    job_status.start("archive"); job_status.finish("archive", success=True)
    real_log = job_status.log_line
    def hooked(name, message):
        real_log(name, message)
        if name == "archive" and "이동 완료" in message: cancel.request()
    job_status.log_line = hooked
    try: await archive_job.run_archive_job()
    finally: job_status.log_line = real_log
    log = "\n".join(job_status.snapshot()["archive"]["log"])
    assert left(src) == ["0002 2화.zip", "0003 3화.zip", "0004 4화.zip"], (left(src), log)                  # 첫 파일은 옮기고(요청이 비워졌다는 증거) 나머지는 그대로
    assert "사용자 요청으로 중단했습니다" in log and job_status.snapshot()["archive"]["status"] == "success", log
asyncio.run(periodic_job())
print("9) 정기 작업 중단 OK")
print("ALL OK")
