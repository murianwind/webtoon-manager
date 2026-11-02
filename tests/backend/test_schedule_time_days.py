"""스케줄의 요일을 "시간마다" 고른다 — 신작 스캔/다운로드/아카이빙 모든 "특정 시각" 스케줄에 같은 방식(월~일 칩이 시각 옆에, 아무 요일도 안 고르면 매일).

- 저장 형식: cron_times의 항목마다 days. 예전에 저장된 공통 요일(cron_days)은 읽을 때 요일이 없는 시각마다 복사해서 동작이 그대로 유지된다.
- 실제 실행: 시각마다 자기 요일로만 돈다(APScheduler CronTrigger를 시각마다 하나씩).
- 아카이빙은 다운로드보다 10분 뒤여야 한다는 검사도 요일별로 — 같은 날(또는 다음 날 새벽으로 넘어가는 경우)에 겹칠 때만 거부한다.
"""
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app import db, repository, schedule_config
from app.schedule_config import JobSchedule
from app.scheduler import schedule as sched_mod
db.get_connection()
import app.main as m

KST = ZoneInfo("Asia/Seoul")
D = JobSchedule()

# Scenario 1: 정리 — 시각마다 요일(월~일 순서, 잘못된 값/중복 제거). 옛 공통 요일(cron_days)은 요일이 "없는" 시각에만 복사하고(요일이 있으면 빈 목록 포함 그대로), 더는 저장하지 않는다
s = schedule_config.from_dict({"mode": "cron", "cron_times": [{"hour": 7, "minute": 0, "days": ["fri", "mon", "mon", "xyz"]}, {"hour": 9, "minute": 30, "days": []}, {"hour": 25, "minute": 5, "days": ["sun"]}], "cron_days": ["sat"]})
assert s.cron_times == [{"hour": 7, "minute": 0, "days": ["mon", "fri"]}, {"hour": 9, "minute": 30, "days": []}, {"hour": 23, "minute": 5, "days": ["sun"]}], s.cron_times
assert not hasattr(s, "cron_days")
legacy = schedule_config.from_dict({"mode": "cron", "cron_times": [{"hour": 4, "minute": 30}, {"hour": 5, "minute": 0}], "cron_days": ["sun", "sat"]})
assert legacy.cron_times == [{"hour": 4, "minute": 30, "days": ["sat", "sun"]}, {"hour": 5, "minute": 0, "days": ["sat", "sun"]}]
assert JobSchedule(mode="cron", cron_times=[{"hour": 3, "minute": 0}]).sanitized().cron_times == [{"hour": 3, "minute": 0, "days": []}]          # 요일 정보가 전혀 없으면 매일
assert schedule_config.from_dict({"mode": "cron", "cron_hour": 6, "cron_minute": 15}).cron_times == [{"hour": 6, "minute": 15, "days": []}]      # 더 옛날 단일 시각 형식도 그대로
print("1) 정리/옛 설정 변환 OK")

# Scenario 2: 예전에 저장된 설정(공통 요일, 시각에는 요일 없음)을 읽으면 시각마다 요일이 채워진다
repository.set_setting("schedule_discovery_job", json.dumps({"mode": "cron", "cron_times": [{"hour": 4, "minute": 30}], "cron_days": ["sat", "sun"]}))
got = schedule_config.get_schedule("discovery_job", D)
assert got.cron_times == [{"hour": 4, "minute": 30, "days": ["sat", "sun"]}]
print("2) 저장된 옛 설정 읽기 OK")

# Scenario 3: 실제 실행 시각 — 시각마다 자기 요일로만 돈다(월·금 07:00, 매일 09:30). 2026-10-12는 월요일
trig = sched_mod._build_trigger(JobSchedule(mode="cron", cron_times=[{"hour": 7, "minute": 0, "days": ["mon", "fri"]}, {"hour": 9, "minute": 30, "days": []}]).sanitized())
fields = lambda t: {f.name: str(f) for f in t.fields}
assert [fields(t)["day_of_week"] for t in trig.triggers] == ["mon,fri", "*"], [fields(t)["day_of_week"] for t in trig.triggers]
def nxt(after): return trig.get_next_fire_time(None, after).astimezone(KST).strftime("%a %H:%M")
assert nxt(datetime(2026, 10, 12, 6, 0, tzinfo=KST)) == "Mon 07:00"        # 월요일 아침: 월·금 07:00이 먼저
assert nxt(datetime(2026, 10, 12, 8, 0, tzinfo=KST)) == "Mon 09:30"        # 07:00은 지났고 매일 09:30
assert nxt(datetime(2026, 10, 12, 10, 0, tzinfo=KST)) == "Tue 09:30"       # 화요일은 매일 09:30만(07:00은 월·금에만)
assert nxt(datetime(2026, 10, 15, 8, 0, tzinfo=KST)) == "Thu 09:30" and nxt(datetime(2026, 10, 15, 10, 0, tzinfo=KST)) == "Fri 07:00"
print("3) 시각마다 요일로 실행 OK")


async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        cron = lambda times, **k: {"mode": "cron", "interval_minutes": 60, "target": "naver", "cron_times": times, **k}
        off = {"mode": "off", "interval_minutes": 60, "cron_times": [{"hour": 3, "minute": 0}], "target": "naver"}
        def payload(download=None, archive=None, discovery=None): return {"discovery_job": discovery or off, "download_job": download if download is not None else [], "report_job": off, "archive_job": archive or off}
        # Scenario 4: API로 시각마다 요일을 저장/조회한다 — 응답에는 cron_days가 없다
        times = [{"hour": 7, "minute": 0, "days": ["mon", "fri"]}, {"hour": 9, "minute": 30, "days": []}]
        r = await c.post("/api/settings", json=payload(discovery=cron(times))); assert r.status_code == 200, r.text
        got = (await c.get("/api/settings")).json()["discovery_job"]
        assert got["cron_times"] == times and "cron_days" not in got, got
        # 옛 화면이 보낸 형태(공통 요일 + 요일 없는 시각)도 받아서 시각마다 요일로 바꿔 저장한다
        r = await c.post("/api/settings", json=payload(discovery=cron([{"hour": 4, "minute": 0}, {"hour": 5, "minute": 0}], cron_days=["sat", "sun"]))); assert r.status_code == 200, r.text
        assert (await c.get("/api/settings")).json()["discovery_job"]["cron_times"] == [{"hour": 4, "minute": 0, "days": ["sat", "sun"]}, {"hour": 5, "minute": 0, "days": ["sat", "sun"]}]
        assert (await c.post("/api/settings", json=payload(discovery=cron([{"hour": 4, "minute": 0, "days": ["someday"]}])))).status_code == 422          # 알 수 없는 요일
        # 다운로드/아카이빙도 같은 방식 — 다운로드 스케줄마다 시각마다 요일
        r = await c.post("/api/settings", json=payload(download=[cron([{"hour": 4, "minute": 0, "days": ["tue"]}], target="kakao")])); assert r.status_code == 200
        assert (await c.get("/api/settings")).json()["download_job"][0]["cron_times"] == [{"hour": 4, "minute": 0, "days": ["tue"]}]
        print("4) 스케줄 API(시각마다 요일) OK")

        # Scenario 5: 아카이빙은 다운로드보다 10분 뒤여야 한다 — 요일별로 겹칠 때만 거부(자정을 넘기는 경우 포함)
        def check(dl_time, dl_days, ar_time, ar_days, **dl_k):
            download = cron([{"hour": dl_time[0], "minute": dl_time[1], "days": dl_days}], **dl_k)
            archive = cron([{"hour": ar_time[0], "minute": ar_time[1], "days": ar_days}])
            return c.post("/api/settings", json=payload(download=[download], archive=archive))
        r = await check((4, 0), ["mon"], (4, 5), ["mon"]); assert r.status_code == 422 and "10분" in r.json()["detail"] and "04:05" in r.json()["detail"] and "04:00" in r.json()["detail"], r.text     # 같은 날 5분 뒤
        assert "월" in r.json()["detail"]                                                                              # 어느 요일인지도 알려 준다
        assert (await check((4, 0), ["mon"], (4, 5), ["tue"])).status_code == 200                                      # 다른 요일이면 겹치지 않는다
        assert (await check((4, 0), ["mon"], (4, 10), ["mon"])).status_code == 200                                     # 정확히 10분 뒤는 허용
        assert (await check((23, 55), ["mon"], (0, 2), ["tue"])).status_code == 422                                    # 월 23:55 다운로드 → 화 00:02 아카이빙(7분 뒤, 자정을 넘음)
        assert (await check((23, 55), ["mon"], (0, 2), ["wed"])).status_code == 200                                    # 수요일 00:02는 괜찮다
        assert (await check((23, 55), ["sun"], (0, 2), ["mon"])).status_code == 422                                    # 일 23:55 → 월 00:02(주를 넘어가는 경우)
        assert (await check((4, 0), [], (4, 5), [])).status_code == 422                                                # 요일 없음 = 매일 — 예전과 같이 거부
        assert (await check((4, 0), [], (4, 5), ["sat"])).status_code == 422                                           # 한쪽이 매일이면 그 요일에 겹친다
        assert (await check((4, 0), ["mon"], (3, 55), ["mon"])).status_code == 200                                     # 아카이빙이 먼저(다운로드 5분 전)는 허용 — 예전 규칙 그대로
        r = await check((4, 0), ["mon"], (4, 5), ["mon"]); r2 = await c.post("/api/settings", json=payload(download=[cron([{"hour": 4, "minute": 0, "days": ["mon"]}], mode="interval")], archive=cron([{"hour": 4, "minute": 5, "days": ["mon"]}])))
        assert r2.status_code == 200                                                                                   # 다운로드가 "몇 분마다"면 검사하지 않는다(예전과 같음)
        print("5) 아카이빙 10분 간격 요일별 검사 OK")
asyncio.run(api())
print("ALL OK")
