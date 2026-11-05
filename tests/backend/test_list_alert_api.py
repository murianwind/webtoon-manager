"""설정 API — 웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)의 발송 시각(GET/POST /api/settings/list-alert).
사용자가 정한 시각(time)과 기본 시각(default_time), 화면이 "시각 입력칸을 보일지" 판단하는 값(디스코드 설정 여부, 발송하지 않는 시간 설정 여부), 지금 적용되는 방식(mode)을 돌려준다."""
import asyncio

import httpx

from app import db, discord_config, list_alert, report_dispatch
db.get_connection()
import app.main as m

q = {"start_hour": 23, "start_minute": 0, "end_hour": 6, "end_minute": 0, "days": []}

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        get = lambda: c.get("/api/settings/list-alert")
        # 처음: 정한 시각 없음, 기본 09:00, 디스코드 없음, 발송하지 않는 시간 없음 → 시각 방식(기본 시각)
        body = (await get()).json()
        assert body == {"time": None, "default_time": "07:00", "discord_configured": False, "quiet_hours_set": False, "mode": "time"}, body
        # 디스코드 설정 / 시각 저장(한 자리 시도 맞춰 저장) / 지우기
        discord_config.set_webhook_url("https://discord.test/hook")
        r = await c.post("/api/settings/list-alert", json={"time": "7:05"}); assert r.status_code == 200 and r.json()["time"] == "07:05" and r.json()["discord_configured"] is True, r.text
        assert (await get()).json()["time"] == "07:05" and list_alert.get_time() == "07:05"
        r = await c.post("/api/settings/list-alert", json={"time": None}); assert r.json()["time"] is None and list_alert.get_time() is None
        # 잘못된 값은 400(이유 포함), 저장된 값은 그대로
        await c.post("/api/settings/list-alert", json={"time": "18:30"})
        for bad in ("25:00", "12:60", "abc"):
            r = await c.post("/api/settings/list-alert", json={"time": bad}); assert r.status_code == 400 and "00:00~23:59" in r.json()["detail"], r.text
        assert (await get()).json()["time"] == "18:30"
        # 발송하지 않는 시간을 설정하면 — 정한 시각이 없으면 발송하지 않는 시간 방식(quiet), 정한 시각이 있으면 그 시각을 유지(time)
        report_dispatch.set_quiet_hours([q])
        body = (await get()).json(); assert body["quiet_hours_set"] is True and body["mode"] == "time" and body["time"] == "18:30", "먼저 정한 시각을 유지"
        await c.post("/api/settings/list-alert", json={"time": None}); body = (await get()).json(); assert body["mode"] == "quiet" and body["time"] is None, body
        report_dispatch.set_quiet_hours([])
        assert (await get()).json()["mode"] == "time" and (await get()).json()["quiet_hours_set"] is False
asyncio.run(main())
print("ALL OK")
