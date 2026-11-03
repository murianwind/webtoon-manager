"""빠른 상태 스캔 작업(scheduler/quick_scan_job.py) — 수동 실행 버튼, 전체목록 새로고침(범위 지정), 매일 새벽 자동 실행이 같이 쓰는 작업.
하는 일: 대상 고르기 → 스캔 → 결과를 작업 로그(실행 이력)에 남긴다. 화면에 띄우던 두 문구("기타 원인 …", "완결 웹툰 N개는 제외됨 목록에 표시하지 않습니다")도 여기서 로그로 남긴다.
겹치는 실행은 건너뛰고, 실패하면 "오류"로 끝난다. 매일 새벽 0시 30분(한국시간)에 자동으로 돈다(설정 화면에는 따로 두지 않는다).
"""
import asyncio

import httpx
from apscheduler.triggers.cron import CronTrigger

from app import db, job_status, quick_scan, repository
db.get_connection()
import app.main as m
import app.api.jobs as jobs_api
import app.scheduler as scheduler
from app.scheduler import quick_scan_job as qj
from app.quick_scan import ScanStats, Target

status = lambda: job_status.snapshot()["quick_scan"]
text = lambda: "\n".join(status()["log"])

# ── 가짜: 대상 고르기/스캔 ──
TARGETS = [Target("naver", "n1", "가", "etc"), Target("kakao", 1, "나", "etc"), Target("naver", "n2", "다", "tue")]
calls = {}
def fake_collect(*, today, max_age_hours, scope=None, now=None):
    calls["collect"] = {"today": today, "max_age_hours": max_age_hours, "scope": scope}
    return list(TARGETS), 4
async def fake_scan(targets, settings, progress=None):
    calls["scan"] = [t.title_id for t in targets]
    if progress: progress("빠른 상태 스캔 진행 3/3개")
    return ScanStats(scanned=3, failed=1, resolved_days=2, newly_finished=1)
quick_scan.collect_targets, quick_scan.scan = fake_collect, fake_scan
quick_scan.today_key = lambda now=None: "tue"
run = lambda *a, **k: asyncio.run(qj.run_quick_scan_job(*a, **k))

# 화면에 띄우던 문구가 이 로그로 옮겨 온다: DB에 "기타 원인"과 "제외됨에서 가려지는 작품"이 있게 해 둔다
repository.upsert_new("x1", "제외-완결"); repository.set_status("x1", repository.STATUS_EXCLUDED); repository.mark_finished("x1")
repository.upsert_new("x2", "제외-요일모름"); repository.set_status("x2", repository.STATUS_EXCLUDED)

# Scenario 1: 전체 실행 — 시작(대상 수, 플랫폼별, 기타 먼저/오늘부터), 건너뛴 수, 진행, 완료 요약, 이력 문장 두 줄. 성공으로 끝난다
run()
assert calls["collect"] == {"today": "tue", "max_age_hours": qj.MANUAL_MAX_AGE_HOURS, "scope": None} and calls["scan"] == ["n1", 1, "n2"], calls
assert status()["status"] == "success"
lines = status()["log"]
assert "빠른 상태 스캔 시작 — 대상 3개 (네이버 2개, 카카오 1개): 요일을 모르는 기타 2개를 먼저, 이어서 오늘(화)부터 요일별로 확인합니다" in text(), text()
assert "최근 1시간 안에 확인한 4개는 건너뜁니다" in text() and "빠른 상태 스캔 진행 3/3개" in text()
assert "빠른 상태 스캔 완료 — 확인 3개: 요일 새로 알아냄 2개, 새로 완결·서비스 종료·판매 중지 1개, 확인 실패 1개" in text(), text()
assert any("기타 원인 — 네이버" in l and "완결 1" in l and "요일 확인 대기 1" in l for l in lines), "기타 원인 한 줄이 실행 이력에 남는다: " + text()
assert any("제외됨 완결·서비스 종료·판매 중지 웹툰 1개는 제외됨 목록에 표시하지 않습니다(필요하면 수동 다운로드에서 분석해서 구독할 수 있습니다)" in l for l in lines), text()
print("1) 전체 실행 OK")

# Scenario 2: 범위를 지정한 실행(전체목록 새로고침) — 범위를 넘겨 그 그룹만, 로그에 범위(기타, 화)를 남긴다. 건너뛴 게 없으면 그 줄은 없다
def collect_none_skipped(**k): calls["collect"] = k; return list(TARGETS[:2]), 0
quick_scan.collect_targets = collect_none_skipped
run({"etc", "tue"}, 1); assert calls["collect"]["scope"] == {"etc", "tue"} and calls["collect"]["max_age_hours"] == 1
assert "범위: 기타, 화" in text() and "건너뜁니다" not in text(), text()
print("2) 범위 지정 OK")

# Scenario 3: 확인할 작품이 없으면 그렇다고 남기고(이력 문장은 그대로), 성공으로 끝난다
quick_scan.collect_targets = lambda **k: ([], 7); calls.pop("scan", None)
run(); assert "scan" not in calls and "빠른 상태 스캔 완료" not in text(), "대상이 없으면 스캔도 하지 않는다"
assert status()["status"] == "success" and "확인할 작품이 없습니다" in text() and "최근 1시간 안에 확인한 7개는 건너뜁니다" in text() and "기타 원인" in text(), text()
print("3) 대상 없음 OK")

# Scenario 4: 스캔이 예외로 실패하면 "오류"로 끝나고 오류를 남긴다. 겹치는 실행은 건너뛴다(시간 제한 안에 끝나야 한다)
quick_scan.collect_targets = fake_collect
async def boom(targets, settings, progress=None): raise RuntimeError("스캔 장애")
quick_scan.scan = boom; run(); assert status()["status"] == "error" and "오류: 스캔 장애" in text(), text()
quick_scan.scan = fake_scan
async def overlap():
    async with qj._quick_scan_lock: await qj.run_quick_scan_job()
calls.pop("scan", None); asyncio.run(asyncio.wait_for(overlap(), 3)); assert "scan" not in calls and "이미 실행 중이라 건너뜁니다" in text()
print("4) 실패/겹침 OK")

# Scenario 5: 매일 새벽 0시 30분(한국시간)에 자동으로 돈다 — 12시간 안에 확인한 작품은 건너뛴다. 사용자 설정 없이 고정된 내부 작업
sched = scheduler.create_scheduler(); job = sched.get_job("quick_scan_nightly")
assert job is not None and isinstance(job.trigger, CronTrigger) and str(job.trigger.timezone) == "Asia/Seoul", job
fields = {f.name: str(f) for f in job.trigger.fields}; assert fields["hour"] == "0" and fields["minute"] == "30", fields
assert job.max_instances == 1 and job.func.__name__ == "run_quick_scan_nightly"
quick_scan.collect_targets = fake_collect; asyncio.run(qj.run_quick_scan_nightly()); assert calls["collect"]["max_age_hours"] == qj.NIGHTLY_MAX_AGE_HOURS == 12 and calls["collect"]["scope"] is None
print("5) 매일 새벽 자동 실행 OK")

# Scenario 6: 수동 실행 API — POST /api/jobs/quick-scan/run: 범위 없이/있이, 잘못된 범위는 400
started = []
async def fake_job(scope=None, max_age_hours=qj.MANUAL_MAX_AGE_HOURS): started.append(scope)
jobs_api.scheduler_mod.run_quick_scan_job = fake_job
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        assert (await c.post("/api/jobs/quick-scan/run")).json() == {"status": "started"}
        assert (await c.post("/api/jobs/quick-scan/run", json={"scope": ["etc", "tue"]})).status_code == 200
        assert (await c.post("/api/jobs/quick-scan/run", json={"scope": ["엉뚱"]})).status_code == 400
        assert (await c.post("/api/jobs/quick-scan/run", json={})).status_code == 200
        await asyncio.sleep(0.05)
asyncio.run(api()); assert started == [None, {"etc", "tue"}, None], started
print("ALL OK")
