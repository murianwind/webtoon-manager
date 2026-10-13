"""네이버 회차 목록의 일부 페이지를 못 받았을 때(fetch_all_episodes가 NaverApiError) — 불완전한 목록으로 진행하지 않고 그 작품만 건너뛰거나 이유를 알린다.

- 자동 다운로드: 그 작품만 이번 실행에서 건너뛰고(로그에 이유) 다른 작품과 작업 전체는 계속된다(예전에는 오류가 작업 전체로 올라갈 수 있었다).
- 수동 분석: 502와 이유를 돌려준다(500이 아니라).
- 수동 다운로드: 작업 상태에 실패 이유를 남기고 멈춘다.
"""
import asyncio

import httpx

from app import db, job_status, manual_download, naver_api, repository
from app.config import get_settings
from app.models import TitleInfo
from app.scheduler import naver_download
db.get_connection()
import app.main as m

TID = "4242"
INFO = TitleInfo(title_id=TID, title_name="목록 실패 작품", synopsis="", is_adult=False, webtoon_type="webtoon", is_finished=False, thumbnail_url="")


async def fake_info(session, title_id, timeout): return INFO
async def failing_episodes(session, title_id, cookies, timeout): raise naver_api.NaverApiError("회차 목록 2페이지를 받지 못했습니다 (titleId=4242, 전체 3페이지)")
naver_api.fetch_title_info, naver_api.fetch_all_episodes = fake_info, failing_episodes
repository.upsert_new(TID, "목록 실패 작품")


async def main():
    settings = get_settings()
    # Scenario 1: 자동 다운로드 계획 — 오류를 올리지 않고 None(이번엔 건너뜀)이며, 작업 로그에 이유가 남는다
    job_status.start("download")
    plan = await naver_download._plan_naver_download(None, settings, repository.get(TID), None)
    assert plan is None
    lines = " ".join(job_status.snapshot()["download"]["log"]) if isinstance(job_status.snapshot().get("download", {}).get("log"), list) else str(job_status.snapshot())
    assert "목록 실패 작품" in lines and "회차 목록" in lines and "건너뜀" in lines, lines
    # Scenario 2: 수동 분석 API — 502와 이유
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = await c.get("/api/manual-download/analyze", params={"title_id": TID})
        assert r.status_code == 502 and "2페이지" in r.json()["detail"], (r.status_code, r.text)
    # Scenario 3: 수동 다운로드 — 작업 상태에 실패 이유를 남기고 멈춘다(예외로 끝나지 않는다)
    job_status.start("manual")
    await manual_download.download_selected(TID, [1], settings)
    state = job_status.snapshot()["manual"]
    assert state["status"] == "error" and "2페이지" in " ".join(state["log"]), state
    print("ALL OK")

asyncio.run(main())
