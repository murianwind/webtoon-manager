"""앱 시작/종료 연결(app/main.py의 lifespan) — 시작할 때 무엇이 연결되는지 고정한다.

테스트는 앱 객체를 직접 만져서 기능을 확인하지만, 실제 서버는 lifespan을 거쳐 시작한다. 여기가 빠지면(예: 작업 종료 연결을 빼먹음) 기능 테스트는 모두 통과하는데 실제로는 동작하지 않는다.
- 시작: 환경변수 경고, 기본 태그, 디스코드 봇, 받은 회차 기록 만들기(실패해도 계속), 리포트 연결(작업 종료 알림 + 옛 발송 시각 설정 정리), 스케줄러(고정 작업), 카카오 목록 갱신(켜져 있을 때만)
- 종료: 스케줄러 종료, 디스코드 봇 종료
- 정적 파일: 화면 파일은 항상 서버에 확인하게(Cache-Control: no-cache) — 예전에 브라우저 캐시 때문에 예전 화면이 계속 보인 적이 있다
"""
import logging
import os

from starlette.testclient import TestClient

from app import config, db, job_status, kakao_catalog, kakao_records, report_dispatch, repository, waitfree_sync
db.get_connection()
import app.main as main

calls = {"start_bot": 0, "stop_bot": 0, "backfill": 0, "refresh": 0}
async def fake_start_bot(): calls["start_bot"] += 1
async def fake_stop_bot(): calls["stop_bot"] += 1
main.start_bot, main.stop_bot = fake_start_bot, fake_stop_bot                                                                       # 네트워크로 나가는 디스코드 봇은 막는다
def fake_backfill(settings): calls["backfill"] += 1
kakao_records.backfill_and_log = fake_backfill
kakao_catalog.start_refresh = lambda: calls.__setitem__("refresh", calls["refresh"] + 1)


class Records(logging.Handler):
    def __init__(self): super().__init__(); self.records = []
    def emit(self, record): self.records.append(record)
log_records = Records(); logging.getLogger("app.main").addHandler(log_records); logging.getLogger("app.main").setLevel(logging.INFO)


def startup(**settings):
    """앱을 시작해서(lifespan 진입) 돌려준다 — with 문 안에서 쓴다. settings는 시작 전에 저장해 둘 설정."""
    for k in calls: calls[k] = 0
    log_records.records.clear()
    for key, value in settings.items(): repository.set_setting(key, value)
    return TestClient(main.app)


# Scenario 1: 기본 시작 — 모든 연결이 되고, 고정 작업이 등록되고, 옛 설정은 정리되고, 화면/API가 응답한다. 종료하면 스케줄러와 봇이 멈춘다
repository.set_setting("schedule_report_job", '{"mode": "cron"}')                                                                  # 예전 "리포트 발송 시각" 설정이 남아 있던 설치
job_status._finish_listeners.clear()
repository.set_setting("kakao_webtoons_enabled", "1")
with startup() as client:
    scheduler = main.app.state.scheduler
    assert scheduler.running, "스케줄러가 시작돼야 한다"
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert "report_tick" in jobs and jobs["report_tick"].trigger.interval.total_seconds() == 60 and jobs["report_tick"].max_instances == 1, list(jobs)      # 리포트 발송 확인(1분)
    assert "kakao_catalog_refresh" in jobs and "waitfree_job" not in jobs, list(jobs)
    assert "quick_scan_nightly" in jobs and jobs["quick_scan_nightly"].max_instances == 1, list(jobs)                                # 매일 새벽 빠른 상태 스캔(고정 작업)                                               # 고정 작업은 있고, 꺼 둔 기다무 작업은 없다
    assert repository.get_setting("schedule_report_job") is None, "옛 리포트 발송 시각 설정이 정리돼야 한다"
    assert report_dispatch.on_job_finished in job_status._finish_listeners, "다운로드 작업이 끝나면 리포트가 알도록 연결돼야 한다"
    assert calls == {"start_bot": 1, "stop_bot": 0, "backfill": 1, "refresh": 1}, calls                                              # 카카오 관리가 켜져 있으니 목록 갱신을 시작
    tags = repository.list_watched_tags(); assert tags and all(not t.enabled for t in tags), "기본 태그는 꺼진 채로 채워진다"
    # 정적 파일 — 항상 서버에 확인(no-cache), 없는 파일은 404. API도 응답한다
    index = client.get("/"); assert index.status_code == 200 and index.headers["cache-control"] == "no-cache" and "웹툰 구독 관리" in index.text
    js = client.get("/js/00-core.js"); assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"
    assert client.get("/style.css").headers["cache-control"] == "no-cache" and client.get("/js/없는파일.js").status_code == 404
    assert client.get("/api/settings").status_code == 200
assert not scheduler.running and calls["stop_bot"] == 1, "종료하면 스케줄러와 봇이 멈춘다"
print("1) 기본 시작/종료 OK")

# Scenario 2: 카카오 관리가 꺼져 있으면 시작할 때 카카오 목록을 갱신하지 않는다. 기다무 옵션이 켜져 있으면 그 작업이 시작할 때 등록된다
repository.set_setting("kakao_webtoons_enabled", None); waitfree_sync.set_enabled(True)
with startup() as client:
    assert calls["refresh"] == 0, "카카오 관리가 꺼져 있으면 갱신하지 않는다"
    assert "waitfree_job" in {j.id for j in main.app.state.scheduler.get_jobs()}, "켜 둔 기다무 옵션은 시작할 때 작업이 등록된다"
waitfree_sync.set_enabled(False)
print("2) 카카오 갱신 조건/기다무 작업 OK")

# Scenario 3: 받은 회차 기록 만들기가 실패해도 시작은 막히지 않는다(오류는 로그에 남는다)
def failing_backfill(settings): calls["backfill"] += 1; raise RuntimeError("폴더를 읽을 수 없음")
kakao_records.backfill_and_log = failing_backfill
with startup() as client:
    assert main.app.state.scheduler.running and client.get("/api/settings").status_code == 200, "실패해도 앱은 시작한다"
    assert calls["backfill"] == 1 and any("받은 회차 기록 만들기 실패" in r.getMessage() and r.exc_info for r in log_records.records), [r.getMessage() for r in log_records.records]
kakao_records.backfill_and_log = fake_backfill
print("3) 기록 만들기 실패에도 시작 OK")

# Scenario 4: 예전 환경변수가 남아 있으면 시작할 때 경고를 남긴다(조용히 무시되지 않게). 없으면 경고 없음
removed = config.REMOVED_ENV_VARIABLES[0]
with startup() as client:
    assert not any("더 이상 쓰이지 않는" in r.getMessage() for r in log_records.records), "없으면 경고하지 않는다"
os.environ[removed] = "1"
try:
    with startup() as client:
        warned = [r for r in log_records.records if "더 이상 쓰이지 않는" in r.getMessage()]
        assert len(warned) == 1 and removed in warned[0].getMessage() and warned[0].levelno == logging.WARNING, [r.getMessage() for r in log_records.records]
finally:
    del os.environ[removed]
print("4) 옛 환경변수 경고 OK")
print("ALL OK")
