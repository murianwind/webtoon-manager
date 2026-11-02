"""기다무 충전 시각에 맞춰 자동 다운로드 — 작품마다 서버가 알려 준 "다음 충전 시각"을 저장해 두고, 그 시각이 되면 그 작품만 받는다(옵션, 기본 꺼짐).

- 충전 시각은 기다무로 회차를 열 때(응답의 waitfree_charged_at)와 아직 충전 전이라는 확인(ticket/my의 charged_at)에서 알게 되어 작품마다 절대 시각(UTC)으로 저장된다.
  기다무가 3시간/1일/3일로 작품마다 달라도 서버가 준 시각을 그대로 쓰므로 따로 계산할 필요가 없고, 컨테이너를 재시작해도 남는다.
- 옵션이 켜지면 정기 스케줄 다운로드는 충전 시각 전인 작품을 건너뛴다(수동 실행은 그대로 전부). 1분마다 도는 확인은 서버에 요청하지 않고 저장된 시각만 보고, 시각이 지난 작품만 한 개씩 받는다.
- 서버 부담/밴 방지: 시각 직후가 아니라 작품마다 정해진 30~120초 뒤에 받고, 충전 시각이 그대로면 10분, 오류/로그인 문제면 30분 뒤로 미룬다(같은 작품을 계속 두드리지 않는다).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp
import httpx

from app import db, job_status, kakao_page_download as kp, repository, scheduler, waitfree_sync
from app.config import get_settings
from app.scheduler import kakao_download, waitfree_job
db.get_connection()
import app.main as m

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
iso = lambda dt: dt.astimezone(UTC).isoformat()
ST = get_settings()
for sid, title in ((501, "삼시간"), (502, "하루"), (503, "사흘"), (504, "시각모름")): repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)
repository.upsert_new_kakao_webtoon(505, "구독해제됨", status=repository.STATUS_UNSUBSCRIBED)
nxt = lambda sid: repository.get_kakao_webtoon(sid)["waitfree_next_at"]

# Scenario 1: 저장소 — 작품마다 절대 시각(UTC)으로 저장하고, 시각 순 목록은 구독 중이고 시각을 아는 작품만
assert nxt(501) is None
waitfree_sync.remember(501, "2026-10-13T21:00:00+09:00")                      # 서버가 주는 형식(+09:00) → UTC로 정규화
assert nxt(501) == "2026-10-13T12:00:00+00:00"
waitfree_sync.remember(502, iso(NOW + timedelta(days=1))); waitfree_sync.remember(503, iso(NOW + timedelta(days=3)))
waitfree_sync.remember(505, iso(NOW + timedelta(hours=1)))                     # 구독 중이 아니면 목록에 안 나온다
assert [w["title_id"] for w in repository.list_kakao_waitfree_schedule()] == [501, 502, 503]
waitfree_sync.remember(501, "읽을 수 없는 값"); assert nxt(501) == "2026-10-13T12:00:00+00:00"          # 못 읽는 값으로 덮어쓰지 않는다
waitfree_sync.remember(501, None); assert nxt(501) == "2026-10-13T12:00:00+00:00"                         # 시각을 모르면(None) 그대로
waitfree_sync.forget(502); assert nxt(502) is None                                                        # 더 기다릴 필요가 없으면 지운다
waitfree_sync.remember(502, iso(NOW + timedelta(days=1)))
print("1) 저장소 OK")

# Scenario 2: 다운로드 예정 시각 = 충전 시각 + 작품마다 정해진 30~120초(같은 값이면 항상 같다 — 틱마다 달라지지 않는다), 이 시각이 지나야 "받을 때"다
t = "2026-10-13T12:00:00+00:00"
for sid in (501, 502, 503, 504):
    delay = (waitfree_sync.due_at(sid, t) - datetime.fromisoformat(t)).total_seconds()
    assert 30 <= delay <= 120 and delay == (waitfree_sync.due_at(sid, t) - datetime.fromisoformat(t)).total_seconds()
assert len({(waitfree_sync.due_at(s, t) - datetime.fromisoformat(t)).total_seconds() for s in range(1000, 1040)}) > 5      # 작품마다 다르다(한꺼번에 몰리지 않게)
w501 = repository.get_kakao_webtoon(501)
assert waitfree_sync.is_waiting(w501, waitfree_sync.due_at(501, nxt(501)) - timedelta(seconds=1)) and not waitfree_sync.is_waiting(w501, waitfree_sync.due_at(501, nxt(501)))
assert not waitfree_sync.is_waiting(repository.get_kakao_webtoon(504), NOW)                                    # 시각을 모르는 작품은 기다리는 중이 아니다(정기 다운로드가 처리)
print("2) 예정 시각 OK")


# ── 가짜 클라이언트/서버(회차 받기는 하지 않고, 부르는 쪽의 판단만 본다) ──
class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class LoginClient:
    cookies, cookies_changed = {}, False
    def __init__(self, logged_in=True): self.logged_in = logged_in
    async def check_login(self): return self.logged_in
real_session = aiohttp.ClientSession
aiohttp.ClientSession = FakeSession
kp.client_from_saved_cookies = lambda session, timeout: LoginClient()
kp.persist_refreshed_cookies = lambda client: None
kakao_download.kakao_page_auth.cookie_status = lambda c: {"days_left": 30}
kakao_download.kakao_page_auth.load_cookies = lambda: {}
async def noop(*a, **k): return None
kakao_download.kakao_page_auth.notify_if_needed = noop
kakao_download._notify_kakao_ticket_needed = noop; kakao_download._notify_kakao_caught_up = noop; kakao_download._notify_kakao_newly_finished = noop
_orig_sleep = asyncio.sleep
async def fast_sleep(x): await _orig_sleep(0)
asyncio.sleep = fast_sleep

calls = []
def fake_result(**k): return kp.RunResult([], [], None, kp.DownloadPlan(mode="compare"), **k)
async def fake_title(client, settings, webtoon, root, failures, **k):
    calls.append(webtoon["title_id"]); return RESULTS.get(webtoon["title_id"], fake_result())
kakao_download._download_kakao_title = fake_title
RESULTS = {}
repository.set_setting(waitfree_sync.SETTING_KEY, None)
for sid in (501, 502, 503): waitfree_sync.remember(sid, iso(NOW + timedelta(hours=3)))
waitfree_sync.remember(502, iso(NOW - timedelta(hours=1)))                      # 502는 충전 시각이 이미 지남(= 받을 때), 501/503은 3시간 뒤, 504는 시각 모름


def regular(skip, now=NOW):
    calls.clear(); job_status.start("download")
    asyncio.run(kakao_download._download_kakao_subscriptions(ST, [], skip_waiting=skip, now=now))
    return sorted(calls)

# Scenario 3: 정기 다운로드 — 옵션이 꺼져 있으면(기본) 전부 받는다. 켜졌을 때는 충전 시각 전인 작품(501, 503)만 건너뛴다(시각이 지났거나 모르는 작품은 그대로)
assert not waitfree_sync.is_enabled()
assert regular(skip=False) == [501, 502, 503, 504]
assert regular(skip=True) == [502, 504], regular(skip=True)
assert "충전 시각 전인 기다무 작품 2개는 건너뜁니다" in "\n".join(job_status.snapshot()["download"]["log"])
print("3) 정기 다운로드 건너뛰기 OK")

# Scenario 4: 정기 다운로드를 시작하는 곳 — 스케줄이 부를 때만(scheduled=True) 옵션을 따르고, 수동 실행은 옵션과 상관없이 전부 확인한다
seen = []
async def spy_subs(settings, failures, *, skip_waiting=False, now=None, only=None): seen.append(skip_waiting)
kakao_download_orig = scheduler.download_job._download_kakao_subscriptions; scheduler.download_job._download_kakao_subscriptions = spy_subs
repository.set_setting(waitfree_sync.SETTING_KEY, "1")
async def run_job(scheduled): scheduler.download_job._download_claimed_platforms.clear(); await scheduler.run_download_job("kakao", scheduled=scheduled)
try:
    asyncio.run(run_job(True)); asyncio.run(run_job(False))
    repository.set_setting(waitfree_sync.SETTING_KEY, None); asyncio.run(run_job(True))
finally:
    scheduler.download_job._download_kakao_subscriptions = kakao_download_orig
assert seen == [True, False, False], seen
print("4) 스케줄/수동 구분 OK")

# Scenario 5: 시각 기반 다운로드(1분 틱) — 옵션이 꺼져 있으면 아무것도 안 한다
repository.set_setting(waitfree_sync.SETTING_KEY, None)
for sid in (501, 502, 503): waitfree_sync.remember(sid, iso(NOW - timedelta(hours=1)))
calls.clear(); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW)); assert calls == []
repository.set_setting(waitfree_sync.SETTING_KEY, "1")
# 시각이 지난 작품(+지터)만 한 개씩, 오래 기다린 순으로 받는다(502가 3시간 전, 501이 1시간 전). 아직 안 된 작품(503)과 시각을 모르는 작품(504)은 건드리지 않는다
for sid, hours in ((501, -1), (502, -3), (503, 2)): waitfree_sync.remember(sid, iso(NOW + timedelta(hours=hours)))
calls.clear(); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW)); assert calls == [502, 501], calls
# 충전 시각이 지난 뒤 지터가 지나기 전에는 아직이다
waitfree_sync.remember(503, iso(NOW - timedelta(seconds=10))); calls.clear(); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW)); assert 503 not in calls
print("5) 시각 기반 다운로드 OK")

# Scenario 6: 받을 작품이 없으면 서버 요청도 작업 상태/이력도 만들지 않는다(1분마다 도는 확인이 흔적을 남기지 않는다)
for sid in (501, 502, 503): waitfree_sync.remember(sid, iso(NOW + timedelta(hours=5)))
before = len(repository.list_job_history())
opened = []
kp.client_from_saved_cookies = lambda session, timeout: opened.append(1) or LoginClient()
calls.clear(); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW))
assert calls == [] and opened == [], (calls, opened)
assert len(repository.list_job_history()) == before
print("6) 받을 것이 없으면 조용함 OK")

# Scenario 7: 한 작품이 연속으로 두드려지지 않게 — 충전 시각이 그대로(안 바뀜)면 10분 뒤, 오류면 30분 뒤, 로그인이 풀렸으면 받으려던 작품 전부 30분 뒤
kp.client_from_saved_cookies = lambda session, timeout: LoginClient()
def make_due(*ids): [waitfree_sync.remember(s, iso(NOW - timedelta(hours=1))) for s in ids]
def due_ts(sid): return datetime.fromisoformat(nxt(sid))
for sid in (501, 502, 503): waitfree_sync.remember(sid, iso(NOW + timedelta(hours=5)))
make_due(501); RESULTS = {501: fake_result()}                                  # 받았는데 충전 시각이 그대로(과거) → 10분 뒤로 미룬다
asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW))
assert due_ts(501) == NOW + timedelta(minutes=waitfree_sync.RETRY_MINUTES), nxt(501)
make_due(502); RESULTS = {502: fake_result(error="HTTP 429")}                  # 오류 → 30분
asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW))
assert due_ts(502) == NOW + timedelta(minutes=waitfree_sync.ERROR_MINUTES), nxt(502)
async def fake_title_new_time(client, settings, webtoon, root, failures, **k):    # 정상: 충전 시각이 새로 저장됨(다음 충전은 3시간 뒤) → 그대로 둔다
    calls.append(webtoon["title_id"]); waitfree_sync.remember(webtoon["title_id"], iso(NOW + timedelta(hours=3))); return fake_result()
kakao_download._download_kakao_title = fake_title_new_time
make_due(503); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW)); assert due_ts(503) == NOW + timedelta(hours=3)
kakao_download._download_kakao_title = fake_title
kp.client_from_saved_cookies = lambda session, timeout: LoginClient(logged_in=False)          # 로그인이 풀린 경우 — 아무 작품도 못 받으니 둘 다 30분 뒤
make_due(501, 502); calls.clear(); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW))
assert calls == [] and due_ts(501) == due_ts(502) == NOW + timedelta(minutes=waitfree_sync.ERROR_MINUTES)
kp.client_from_saved_cookies = lambda session, timeout: None                                   # 쿠키가 없는 경우도 같다
make_due(501); asyncio.run(waitfree_job.run_waitfree_due_job(now=NOW)); assert due_ts(501) == NOW + timedelta(minutes=waitfree_sync.ERROR_MINUTES)
kp.client_from_saved_cookies = lambda session, timeout: LoginClient()
print("7) 밴 방지 백오프 OK")

# Scenario 8: 정기/수동 다운로드가 이미 돌고 있으면 이번 틱은 건너뛴다(같은 작품을 동시에 받지 않는다 — 1분 뒤 다시 확인)
RESULTS = {}
make_due(501, 502); calls.clear()
async def with_lock():
    async with scheduler.download_job._download_job_lock:
        await waitfree_job.run_waitfree_due_job(now=NOW)
asyncio.run(asyncio.wait_for(with_lock(), 3)); assert calls == []          # 잠겨 있을 때 기다리거나 동시에 들어가면 멈추므로 제한 시간 안에 끝나야 한다
print("8) 다운로드가 돌고 있으면 건너뜀 OK")


# Scenario 8b: 충전 시각을 알게 되는 곳(러너) — 아직 충전 전이면 서버가 준 시각, 기다무로 열었으면 사용 응답의 다음 충전 시각, 기다릴 잠긴 회차가 없으면 지운다
import tempfile
repository.upsert_new_kakao_webtoon(506, "러너작품", status=repository.STATUS_ACTIVE); repository.set_kakao_downloaded_numbers(506, {1, 2, 3})
class RunnerClient:
    def __init__(self, episodes, ready, available_at=None, charged_at=None):
        self.episodes, self.ready, self.available_at, self.charged_at, self.last_waitfree_charged_at = episodes, ready, available_at, charged_at, None
    async def list_episodes(self, series_id): return {"title": "러너작품", "is_waitfree": True, "on_issue": "Y"}, self.episodes
    async def viewer_image_urls(self, series_id, product_id): return [f"u{product_id}"]
    async def download_image(self, url): return b"x", "image/jpeg"
    async def ticket_info(self, series_id): return kp.TicketInfo(waitfree_ready=self.ready, waitfree_available_at=None if self.ready else self.available_at)
    async def use_waitfree_ticket(self, product_id): self.last_waitfree_charged_at = self.charged_at; return True, ""
    async def fetch_about(self, series_id): return None
async def no_metadata(*a, **k): return None
kp.runner.write_series_metadata = no_metadata
def ep(n, locked=False): return kp.Episode(product_id=n, title=f"러너작품 {n}화", number=n, subtitle=f"{n}화", is_free=not locked, accessible=not locked, page_count=1, hidden=False)
def run_runner(client): return asyncio.run(kp.run_download(client, series_id=506, title=None, download_root=tempfile.mkdtemp(), max_episodes=50))
eps_locked = [ep(1), ep(2), ep(3), ep(4, True), ep(5, True)]
repository.set_kakao_waitfree_next_at(506, None)
run_runner(RunnerClient(eps_locked, ready=False, available_at="2026-10-14T00:00:00+09:00"))
assert nxt(506) == "2026-10-13T15:00:00+00:00", nxt(506)                                  # 아직 충전 전: 서버가 알려 준 시각을 저장
r = run_runner(RunnerClient(eps_locked, ready=True, charged_at="2026-10-14T03:00:00+09:00"))
assert r.ticket_used == 4 and nxt(506) == "2026-10-13T18:00:00+00:00", (r.ticket_used, nxt(506))   # 기다무로 열었다: 사용 응답의 다음 충전 시각을 저장
repository.set_kakao_downloaded_numbers(506, {1, 2, 3, 4, 5})
run_runner(RunnerClient([ep(n) for n in range(1, 6)], ready=False)); assert nxt(506) is None     # 기다릴 잠긴 회차가 없으면 지운다(정기 다운로드가 그대로 확인)
print("8b) 러너가 충전 시각을 저장/삭제 OK")

# Scenario 9: 설정/목록 API와 스케줄러 등록
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        repository.set_setting(waitfree_sync.SETTING_KEY, None)
        assert (await c.get("/api/settings/waitfree-sync")).json() == {"enabled": False}
        r = await c.post("/api/settings/waitfree-sync", json={"enabled": True}); assert r.status_code == 200 and r.json() == {"enabled": True} and waitfree_sync.is_enabled()
        for sid in (501, 502, 503): waitfree_sync.remember(sid, iso(NOW + timedelta(hours={501: 5, 502: 2, 503: 30}[sid])))
        waitfree_sync.remember(504, None)
        body = (await c.get("/api/waitfree/schedule")).json()
        assert body["enabled"] is True and [i["title_id"] for i in body["items"]] == [502, 501, 503], body            # 예정 시각 순, 시각을 모르는 작품/구독해제 작품은 없다
        first = body["items"][0]
        assert first["title"] == "하루" and first["next_at"] == iso(NOW + timedelta(hours=2)) and first["due_at"] == iso(waitfree_sync.due_at(502, first["next_at"]))
        assert (await c.post("/api/settings/waitfree-sync", json={"enabled": False})).json() == {"enabled": False}
        assert (await c.get("/api/waitfree/schedule")).json()["enabled"] is False
asyncio.run(api())
from apscheduler.schedulers.asyncio import AsyncIOScheduler
async def registration():
    s = AsyncIOScheduler(timezone="Asia/Seoul")
    repository.set_setting(waitfree_sync.SETTING_KEY, "1"); scheduler.schedule.apply_waitfree_job(s)
    job = s.get_job(scheduler.schedule.WAITFREE_JOB_ID); assert job is not None and job.trigger.interval == timedelta(minutes=1) and job.max_instances == 1
    scheduler.schedule.apply_waitfree_job(s); assert len([j for j in s.get_jobs() if j.id == scheduler.schedule.WAITFREE_JOB_ID]) == 1       # 다시 불러도 하나
    repository.set_setting(waitfree_sync.SETTING_KEY, None); scheduler.schedule.apply_waitfree_job(s); assert s.get_job(scheduler.schedule.WAITFREE_JOB_ID) is None
    repository.set_setting(waitfree_sync.SETTING_KEY, "1")
    s2 = scheduler.create_scheduler(); assert s2.get_job(scheduler.schedule.WAITFREE_JOB_ID) is not None                               # 시작할 때도 옵션대로
    repository.set_setting(waitfree_sync.SETTING_KEY, None); scheduler.reschedule_all(s2); assert s2.get_job(scheduler.schedule.WAITFREE_JOB_ID) is None   # 스케줄 다시 불러오기에도 반영
asyncio.run(registration())
print("9) API/등록 OK")

# Scenario 10: 백업에 들어가고 복원된다(작품 정보의 한 칸)
snapshot = json.loads(json.dumps(repository.export_all()))
assert any("waitfree_next_at" in row for row in snapshot["kakao_webtoons"])
aiohttp.ClientSession = real_session
print("10) 백업 OK")
print("ALL OK")
