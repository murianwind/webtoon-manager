"""폴더 경로가 루트(ARCHIVE_ROOT/DOWNLOAD_ROOT) 밖으로 못 나가게 막는 방어 — 이 앱은 외부에서 열 수 있어서 경로 조작(../, 절대경로, 심볼릭 링크)이 통하면 서버의 다른 폴더를 읽거나 만들 수 있다.
방어가 세 곳에 따로 있다(api/archive.py의 폴더 조회/생성 두 곳, archiver/transfer.py의 _local_archive_path) — 어느 한 곳이라도 빠지면 바로 드러나게 같은 공격을 모두에게 던진다.
"""
import asyncio
import os
from pathlib import Path

import httpx

from app import db, job_status
from app.archiver.transfer import _local_archive_path
from app.config import get_settings
db.get_connection()
import app.main as m

ROOT = Path(get_settings().archive_root).resolve()
OUTSIDE = ROOT.parent / "outside"                    # 루트의 형제 폴더(바깥)
EVIL_PREFIX = ROOT.parent / (ROOT.name + "_evil")    # 이름이 루트로 "시작"하는 형제 폴더 — startswith로 검사하면 뚫린다
for d in (OUTSIDE, EVIL_PREFIX, ROOT / "inside" / "sub"):
    d.mkdir(parents=True, exist_ok=True)
(OUTSIDE / "secret.txt").write_text("비밀")
os.symlink(OUTSIDE, ROOT / "link_out")               # 루트 안에 있지만 바깥을 가리키는 링크

ATTACKS = ["../outside", "inside/../../outside", f"../{EVIL_PREFIX.name}", str(OUTSIDE), "/etc", "link_out", "link_out/x", "inside/../../../etc"]

# Scenario 1: 공용 검사(_local_archive_path) — 루트 안은 통과(빈 경로/점 포함), 공격은 모두 ValueError
assert _local_archive_path(str(ROOT), "") == ROOT and _local_archive_path(str(ROOT), ".") == ROOT and _local_archive_path(str(ROOT), "inside/sub") == ROOT / "inside" / "sub"
assert _local_archive_path(str(ROOT), "inside/../inside/sub") == ROOT / "inside" / "sub"                                           # 안에서 오르내려도 결과가 루트 안이면 허용
assert _local_archive_path(str(ROOT), "없는폴더/아직") == ROOT / "없는폴더" / "아직"                                                      # 아직 없는 경로도 계산은 한다
for attack in ATTACKS:
    try:
        _local_archive_path(str(ROOT), attack); raise AssertionError(f"막혀야 한다: {attack}")
    except ValueError as e:
        assert "루트 밖" in str(e)
print("1) 공용 검사 OK")


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 2: 폴더 조회(GET /api/archive/folders) — 정상은 통과, 공격은 400. 어느 루트(archive/download)에서도 같다
        ok = await c.get("/api/archive/folders"); assert ok.status_code == 200 and "inside" in [f["name"] for f in ok.json()["folders"]]
        assert (await c.get("/api/archive/folders", params={"path": "inside"})).json()["folders"] == [{"name": "sub", "path": "inside/sub"}]
        assert (await c.get("/api/archive/folders", params={"path": "없는/폴더"})).json() == {"path": "없는/폴더", "folders": []}          # 없는 폴더는 빈 목록
        for attack in ATTACKS:
            r = await c.get("/api/archive/folders", params={"path": attack}); assert r.status_code == 400 and "잘못된 경로" in r.json()["detail"], (attack, r.status_code, r.text)
        assert (await c.get("/api/archive/folders", params={"path": "x", "local_root": "없는루트"})).status_code == 400
        dl = Path(get_settings().download_root).resolve()
        for attack in ("../outside", "/etc"):
            r = await c.get("/api/archive/folders", params={"path": attack, "local_root": "download"}); assert r.status_code == 400, (attack, r.text)   # 다운로드 루트도 같은 방어
        assert (await c.get("/api/archive/folders", params={"path": "", "local_root": "download"})).status_code == 200 and dl.exists()
        print("2) 폴더 조회 방어 OK")

        # Scenario 3: 폴더 만들기(POST /api/archive/folders) — 정상은 만들고, 공격은 400이며 바깥에 아무것도 만들지 않는다
        r = await c.post("/api/archive/folders", json={"path": "new/deeper"}); assert r.status_code == 200 and (ROOT / "new" / "deeper").is_dir()
        before = sorted(p.name for p in ROOT.parent.iterdir()) + sorted(p.name for p in OUTSIDE.iterdir())
        for attack in ("../created_outside", "inside/../../created_outside", f"../{EVIL_PREFIX.name}/made", "link_out/made", str(OUTSIDE / "created_abs")):
            r = await c.post("/api/archive/folders", json={"path": attack}); assert r.status_code == 400 and "잘못된 경로" in r.json()["detail"], (attack, r.status_code, r.text)
        after = sorted(p.name for p in ROOT.parent.iterdir()) + sorted(p.name for p in OUTSIDE.iterdir())
        assert before == after, f"바깥에 뭔가 만들어졌다: {set(after) ^ set(before)}"
        assert (await c.post("/api/archive/folders", json={"path": "x", "root": "없는루트"})).status_code == 422                          # 알 수 없는 루트는 요청 검증에서 거부
        print("3) 폴더 만들기 방어 OK")

        # Scenario 4: 폴더 일괄 이동(원본/목적지 경로) — 바깥을 원본으로 줘도 파일이 옮겨지지 않는다(작업은 백그라운드라 끝난 뒤 확인)
        (ROOT / "dest_ok").mkdir(exist_ok=True)
        for source, dest in (("../outside", "dest_ok"), ("inside", "../outside_dest"), ("link_out", "dest_ok")):
            body = {"source_type": "local", "source_path": source, "source_local_root": "archive", "dest_type": "local", "dest_path": dest, "dest_local_root": "archive"}
            r = await c.post("/api/archive/bulk-move", json=body); assert r.status_code == 200, r.text
            for _ in range(200):                                                                                                # 백그라운드 작업이 끝날 때까지(최대 10초)
                await asyncio.sleep(0.05)
                if job_status.snapshot()["bulk_move"]["status"] != "running": break
            snap = job_status.snapshot()["bulk_move"]
            assert snap["status"] == "error" and "루트 밖" in snap["log"][-1], (source, dest, snap["status"], snap["log"][-2:])   # 공격 입력은 "루트 밖" 오류로 끝난다(성공으로 끝나면 안 된다)
        assert (OUTSIDE / "secret.txt").exists() and not list((ROOT / "dest_ok").iterdir()), "바깥 파일이 옮겨지면 안 된다"
        assert not (ROOT.parent / "outside_dest").exists(), "바깥에 목적지가 만들어지면 안 된다"
        print("4) 일괄 이동 방어 OK")
asyncio.run(main())
print("ALL OK")
