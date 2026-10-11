"""스케줄러가 스케줄 시각보다 조금 늦게 시작해도 작업을 실행한다.

APScheduler 기본값(misfire_grace_time=1초)이면 1초만 늦어도 그 회차를 실행하지 않고 버린다 — 실제로 06:50 다운로드가
"was missed by 0:00:01.17"로 건너뛰어졌다. 허용 시간을 10분으로 두고, 그보다 더 늦은 건 건너뛴다.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db
db.get_connection()
from app.scheduler import create_scheduler

KST = timezone(timedelta(hours=9))
ran = []


async def probe(name):
    ran.append(name)


async def main():
    sched = create_scheduler()
    sched.remove_all_jobs()   # 실제 작업은 돌리지 않고, 시각만 조절한 시험용 작업으로 확인한다
    now = datetime.now(KST)
    sched.add_job(probe, "date", run_date=now - timedelta(seconds=3), args=["3초 늦음"], id="late3s")
    sched.add_job(probe, "date", run_date=now - timedelta(minutes=9), args=["9분 늦음"], id="late9m")
    sched.add_job(probe, "date", run_date=now - timedelta(minutes=11), args=["11분 늦음"], id="late11m")
    sched.start()
    await asyncio.sleep(0.8)

    # Scenario 1: 3초 늦은 것(06:50 때와 같은 상황)과 허용 시간 안(9분)의 늦은 것은 실행한다
    assert "3초 늦음" in ran, ran
    assert "9분 늦음" in ran, ran
    # Scenario 2: 허용 시간(10분)을 넘긴 것은 건너뛴다
    assert "11분 늦음" not in ran, ran
    print("1~2) 지연 허용 시간 동작 OK")

    # Scenario 3: 설정값 — 실제 스케줄 작업에도 같은 허용 시간과 coalesce가 적용된다
    real = create_scheduler()
    real.start()
    jobs = real.get_jobs()
    assert jobs and all(j.misfire_grace_time == 600 and j.coalesce is True for j in jobs), [(j.id, j.misfire_grace_time, j.coalesce) for j in jobs]
    print("3) 실제 작업 설정 OK:", len(jobs), "개")
    real.shutdown(wait=False); sched.shutdown(wait=False)

asyncio.run(main())
print("ALL OK")
