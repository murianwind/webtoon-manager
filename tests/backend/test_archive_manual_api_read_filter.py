"""아카이빙 "지금 이동"(POST /api/archive/run)이 뷰어의 읽음 상태를 쓰는지 — 뷰어 서버 주소가 설정돼 있으면 읽은 회차만 옮기고, 읽음 상태를 알 수 없으면 그 작품은 옮기지 않으며,
완결 처리 이동은 읽음 상태를 보지 않는다. 주소가 없으면 예전처럼 마지막 회차만 남기고 옮긴다. (이동 규칙 자체는 test_archive_skip_unread.py)"""
import asyncio
from pathlib import Path

import httpx

from app import db, job_status, rclone_updater, repository, webtoon_server_client
db.get_connection()
import app.main as m
from app.config import get_settings

async def no_update(*args, **kwargs): return "rclone 업데이트 확인 생략(테스트)"
rclone_updater.check_and_update = no_update                       # 아카이빙 작업은 시작할 때 실제 GitHub에 접속한다 — 테스트에서는 가짜로
st = get_settings()
A = Path(st.archive_root); D = Path(st.download_root); A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)

def make(title, nums):
    d = D / title; d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*.zip"): p.unlink()
    for n in nums: (d / f"{n:04d} {n}화.zip").write_bytes(b"zip")
    return d
left = lambda d: sorted(p.name for p in Path(d).glob("*.zip"))

READ = {"value": frozenset()}
asked = []
async def fake_read(session, base_url, series_name, timeout_seconds):
    asked.append(series_name); return READ["value"]
webtoon_server_client.fetch_read_numbers = fake_read

repository.upsert_new(title_id="911", title="수동API작", added_source=repository.SOURCE_MANUAL); repository.upsert_archive_target("911", "보관911", enabled=True)

async def run_and_wait(c, **body):
    job_status.start("archive"); job_status.finish("archive", success=True)               # 이전 실행 상태를 비운다
    r = await c.post("/api/archive/run", json=body); assert r.status_code == 200
    for _ in range(400):
        await asyncio.sleep(0.05)
        if job_status.snapshot()["archive"]["status"] != "running": break
    else: raise AssertionError("아카이빙이 20초 안에 끝나지 않았다")
    return "\n".join(job_status.snapshot()["archive"]["log"])

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 1: 뷰어 주소가 설정돼 있으면 선택한 웹툰은 읽은 회차만 옮긴다(안 읽은 회차와 마지막 회차는 남음)
        repository.set_setting("webtoon_server_url", "http://viewer:8000"); READ["value"] = frozenset({1, 2})
        src = make("수동API작", [1, 2, 3, 4]); log = await run_and_wait(c, title_ids=["911"])
        assert left(src) == ["0003 3화.zip", "0004 4화.zip"] and "읽지 않은 1개 회차는 건너뜀" in log and asked == ["수동API작"], (left(src), log)
        history = [e for e in repository.list_job_history(50) if e["job_name"] == "viewer_server"]                                                    # 실행 이력("웹툰 서버 연동")에 조회 결과가 남는다
        assert history and "지금 이동(수동) — 웹툰 서버에 읽은 회차를 조회했습니다 (1개 작품: 성공 1, 실패 0)" in history[0]["log"][0] and "- 수동API작: 읽은 회차 2개" in history[0]["log"][1], history
        print("1) 뷰어 설정 시 읽은 회차만 OK")
        # Scenario 2: 선택 없이(활성 대상 전부) 실행해도 같다
        src = make("수동API작", [11, 12, 13]); READ["value"] = frozenset({11}); log = await run_and_wait(c)
        assert left(src) == ["0012 12화.zip", "0013 13화.zip"], left(src)
        print("2) 전체 실행도 OK")
        # Scenario 3: 읽음 상태를 알 수 없으면(조회 실패) 그 작품은 옮기지 않고 로그에 남긴다
        src = make("수동API작", [21, 22, 23]); READ["value"] = None; log = await run_and_wait(c, title_ids=["911"])
        assert left(src) == ["0021 21화.zip", "0022 22화.zip", "0023 23화.zip"] and "읽음 상태를 확인하지 못해" in log, (left(src), log)
        print("3) 조회 실패는 건너뜀 OK")
        # Scenario 4: 완결 처리로 이동은 읽음 상태를 보지 않는다(조회도 안 한다)
        src = make("수동API작", [31, 32]); READ["value"] = frozenset(); asked.clear(); await run_and_wait(c, title_ids=["911"], full_move=True)
        assert not asked and left(src) == [], (asked, left(src))
        print("4) 완결 처리는 읽음 상태 안 봄 OK")
        # Scenario 5: 뷰어 주소가 없으면 예전처럼 마지막만 남기고 옮긴다(조회도 안 한다)
        repository.set_setting("webtoon_server_url", ""); repository.upsert_archive_target("911", "보관911", enabled=True)
        src = make("수동API작", [41, 42, 43]); asked.clear(); await run_and_wait(c, title_ids=["911"])
        assert left(src) == ["0043 43화.zip"] and not asked, (left(src), asked)
        print("5) 뷰어 미설정 OK")
asyncio.run(main())
print("ALL OK")
