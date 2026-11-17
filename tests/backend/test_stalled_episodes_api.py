"""설정 API — 회차가 늘지 않는 카카오 웹툰 알림의 기준 일수(GET/POST /api/settings/stalled-episodes). 기본 30일, 0이면 끔, 0~365만 받는다."""
import asyncio

import httpx

from app import db, repository, stalled_episodes
db.get_connection()
import app.main as m

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = await c.get("/api/settings/stalled-episodes"); assert r.json() == {"days": 30, "default_days": 30, "max_days": 365}, r.text
        r = await c.post("/api/settings/stalled-episodes", json={"days": 45}); assert r.status_code == 200 and r.json()["days"] == 45 and stalled_episodes.get_days() == 45, r.text
        for bad in (-1, 366):
            r = await c.post("/api/settings/stalled-episodes", json={"days": bad}); assert r.status_code == 400 and "0~365" in r.json()["detail"], r.text
        assert (await c.get("/api/settings/stalled-episodes")).json()["days"] == 45, "잘못된 값은 저장하지 않는다"
        # 끄면 0, 다시 켜면 이전 회차 기록이 지워진다
        repository.upsert_new_kakao_webtoon(1, "작품"); repository.set_kakao_episode_count(1, 10, "2026-01-01")
        assert (await c.post("/api/settings/stalled-episodes", json={"days": 0})).json()["days"] == 0 and repository.get_kakao_webtoon(1)["episode_count_seen"] == 10
        assert (await c.post("/api/settings/stalled-episodes", json={"days": 30})).json()["days"] == 30 and repository.get_kakao_webtoon(1)["episode_count_seen"] is None
asyncio.run(main())
print("ALL OK")
