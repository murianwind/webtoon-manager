"""다운로드 스케줄 — 대상(네이버/카카오/둘 다) 선택, 여러 개 등록/수정, 예전 설정 호환, 실행 대상별 동작."""
import asyncio
import json
import httpx

from app import db, repository, schedule_config, scheduler as sch
db.get_connection()
import app.main as m

async def main():
    # ── 1. 저장 형식 ──
    D = sch.DEFAULT_SCHEDULES["download_job"]
    assert [s.target for s in schedule_config.get_download_schedules(D)] == ["naver"]                       # 저장 전: 기본 스케줄 하나(네이버)
    repository.set_setting("schedule_download_job", json.dumps({"mode": "cron", "cron_times": [{"hour": 4, "minute": 30}], "cron_days": ["mon"], "interval_minutes": 60}))   # 예전에 하나만 저장된 설정
    legacy = schedule_config.get_download_schedules(D)
    assert len(legacy) == 1 and legacy[0].target == "naver" and legacy[0].mode == "cron" and legacy[0].cron_times == [{"hour": 4, "minute": 30}]
    repository.set_setting("schedule_download_job", json.dumps({"mode": "cron", "cron_hour": 5, "cron_minute": 10}))    # 더 예전(cron_hour) 형식도
    assert schedule_config.get_download_schedules(D)[0].cron_times == [{"hour": 5, "minute": 10}]
    schedule_config.set_download_schedules([schedule_config.JobSchedule(mode="interval", interval_minutes=45, target="kakao"), schedule_config.JobSchedule(mode="off", target="bogus")])
    got = schedule_config.get_download_schedules(D)
    assert [(s.mode, s.target) for s in got] == [("interval", "kakao"), ("off", "naver")]                        # 잘못된 대상은 네이버로
    schedule_config.set_download_schedules([]); assert schedule_config.get_download_schedules(D) == []             # 빈 목록 = 다운로드 전부 끔
    repository.set_setting("schedule_download_job", None)
    print("1) 저장 형식 OK (예전 설정 호환, 여러 개, 잘못된 대상 보정, 빈 목록)")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 2. API ──
        r = (await c.get("/api/settings")).json()
        assert isinstance(r["download_job"], list) and r["download_job"][0]["target"] == "naver" and isinstance(r["discovery_job"], dict)
        base = {k: v for k, v in r.items()}
        def payload(download):
            return {**base, "download_job": download}
        entries = [
            {"mode": "interval", "interval_minutes": 60, "cron_times": [{"hour": 3, "minute": 0}], "cron_days": [], "target": "naver"},
            {"mode": "cron", "interval_minutes": 60, "cron_times": [{"hour": 4, "minute": 0}, {"hour": 16, "minute": 0}], "cron_days": ["sat", "sun"], "target": "kakao"},
            {"mode": "cron", "interval_minutes": 60, "cron_times": [{"hour": 7, "minute": 0}], "cron_days": [], "target": "both"},
        ]
        r = await c.post("/api/settings", json=payload(entries)); assert r.status_code == 200, r.text
        saved = r.json()["download_job"]
        assert [(s["mode"], s["target"]) for s in saved] == [("interval", "naver"), ("cron", "kakao"), ("cron", "both")] and saved[1]["cron_days"] == ["sat", "sun"]
        assert (await c.get("/api/settings")).json()["download_job"] == saved
        bad = [{**entries[0], "target": "daum"}]
        assert (await c.post("/api/settings", json=payload(bad))).status_code == 422
        # 수정: 두 번째 스케줄의 대상을 바꾸고 첫 번째를 지운다
        edited = [{**entries[1], "target": "both"}, entries[2]]
        assert [s["target"] for s in (await c.post("/api/settings", json=payload(edited))).json()["download_job"]] == ["both", "both"]
        # 아카이빙 10분 간격 검증은 다운로드 스케줄 전부에 적용된다
        archive = {"mode": "cron", "interval_minutes": 60, "cron_times": [{"hour": 4, "minute": 5}], "cron_days": [], "target": "naver"}
        r = await c.post("/api/settings", json={**payload(edited), "archive_job": archive})
        assert r.status_code == 422 and "10분" in r.json()["detail"]                                             # 04:00 다운로드 5분 뒤 → 거부
        ok = {**archive, "cron_times": [{"hour": 4, "minute": 10}, {"hour": 16, "minute": 30}, {"hour": 7, "minute": 10}]}
        assert (await c.post("/api/settings", json={**payload(edited), "archive_job": ok})).status_code == 200
        assert (await c.post("/api/settings", json={**payload([]), "archive_job": ok})).json()["download_job"] == []      # 다운로드 스케줄을 전부 지워도 저장된다
        print("2) 스케줄 API OK (목록 저장/수정/삭제, 대상 검증, 아카이빙 간격 검증)")

        # ── 3. 스케줄러 등록 ──
        await c.post("/api/settings", json=payload(entries))
        s = sch.create_scheduler()
        jobs = {j.id: j for j in s.get_jobs() if j.id.startswith("download_job")}
        assert sorted(jobs) == ["download_job:0", "download_job:1", "download_job:2"]
        assert [jobs[f"download_job:{i}"].kwargs["target"] for i in range(3)] == ["naver", "kakao", "both"]
        await c.post("/api/settings", json=payload([{**entries[0], "mode": "off"}, entries[1]]))                  # 꺼진 스케줄은 등록 안 됨
        sch.reschedule_all(s)
        jobs = {j.id: j for j in s.get_jobs() if j.id.startswith("download_job")}
        assert list(jobs) == ["download_job:1"] and jobs["download_job:1"].kwargs == {"target": "kakao"}       # 지우고 다시 등록(옛 항목은 남지 않음)
        await c.post("/api/settings", json=payload([]))
        sch.reschedule_all(s); assert not [j for j in s.get_jobs() if j.id.startswith("download_job")]
        print("3) 스케줄러 등록 OK (스케줄마다 대상 전달, 꺼진 것/지운 것은 등록 안 됨, 재등록 시 정리)")

        # ── 4. 실행 대상 ──
        ran = []; gate = asyncio.Event(); started = asyncio.Event()
        async def fake_impl(platforms):
            ran.append(sorted(platforms)); started.set(); await gate.wait()
        sch._run_download_job_impl = fake_impl
        t1 = asyncio.create_task(sch.run_download_job("naver")); await started.wait()              # 네이버 실행 중
        t4 = asyncio.create_task(sch.run_download_job("both"))                                       # naver는 이미 맡겨짐 → 남은 kakao만 순서를 기다린다
        t2 = asyncio.create_task(sch.run_download_job("kakao"))                                      # kakao도 이미 맡겨짐 → 건너뜀
        t3 = asyncio.create_task(sch.run_download_job("naver"))                                      # 같은 플랫폼 중복 → 건너뜀
        await asyncio.sleep(0.05); await t2; await t3
        assert ran == [["naver"]]                                                                     # 아직 kakao는 대기 중(동시에 돌지 않는다)
        gate.set(); await asyncio.gather(t1, t4)
        assert ran == [["naver"], ["kakao"]], ran                                                     # 끝나면 대기하던 kakao가 실행됨(중복 실행은 없음)
        assert sch._download_claimed_platforms == set()                                                # 끝나면 맡은 플랫폼이 비워진다
        ran.clear(); gate.set(); await sch.run_download_job("both"); assert ran == [["kakao", "naver"]]
        started_targets = []
        async def rec(target="naver"): started_targets.append(target)
        sch.run_download_job = rec
        for t in ("kakao", None):
            (await c.post("/api/jobs/download/run", params={"target": t} if t else None))
        await asyncio.sleep(0.05); assert started_targets[0] == "kakao" and started_targets[1] == "naver"       # 스케줄 없음 → 예전처럼 네이버만
        await c.post("/api/settings", json=payload([entries[1], entries[0]]))                                     # 네이버+카카오 스케줄이 있으면 둘 다
        await c.post("/api/jobs/download/run"); await asyncio.sleep(0.05); assert started_targets[2] == "both"
        assert (await c.post("/api/jobs/download/run", params={"target": "daum"})).status_code == 400
        print("4) 실행 대상 OK (같은 플랫폼 중복 방지, 다른 플랫폼은 대기 후 실행, 둘 다는 남은 것만, 지금 실행 기본 대상)")
asyncio.run(main())
print("\n전부 통과")
