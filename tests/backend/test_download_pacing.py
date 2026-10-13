"""자동 다운로드 속도 조절 — 작품당 한 번에 받을 회차 상한과 배치 사이 쉬는 시간을 설정 화면에서 바꾼다(재시작 없이 바로 적용).

기본값은 환경변수(Settings: 10회차/5분)이고, 설정 화면에서 바꾼 값(DB)이 있으면 그 값을 쓴다. 잘못된 값은 저장하지 않고, DB에 잘못된 값이 들어 있어도 기본값으로 돌아간다.
"""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import httpx

from app import db, download_pacing, download_roots, job_status, repository, scheduler
from app import kakao_page_auth as auth
from app import kakao_page_download as kp
db.get_connection()
import app.main as m

DEFAULTS = NS(max_new_episodes_per_title=10, batch_rest_minutes=5.0)
sleeps = []
async def fake_sleep(x): sleeps.append(x)
scheduler.asyncio.sleep = fake_sleep


def limits_rules():
    # Scenario 1: 저장한 값이 없으면 환경변수 기본값, 있으면 그 값
    assert download_pacing.limits(DEFAULTS) == (10, 5.0)
    repository.set_setting(download_pacing.CAP_KEY, "3"); repository.set_setting(download_pacing.REST_KEY, "0.5")
    assert download_pacing.limits(DEFAULTS) == (3, 0.5)
    # 0은 "제한 없음"(예전과 같은 뜻)이라 유효하다
    repository.set_setting(download_pacing.CAP_KEY, "0"); assert download_pacing.limits(DEFAULTS) == (0, 0.5)
    # Scenario 2: DB에 이상한 값(범위 밖, 숫자 아님)이 들어 있으면 그 항목만 기본값으로 돌아간다 — 다운로드가 이상한 값으로 돌지 않는다
    repository.set_setting(download_pacing.CAP_KEY, "999"); repository.set_setting(download_pacing.REST_KEY, "abc")
    assert download_pacing.limits(DEFAULTS) == (10, 5.0)
    repository.set_setting(download_pacing.CAP_KEY, "-1"); repository.set_setting(download_pacing.REST_KEY, "0.1")
    assert download_pacing.limits(DEFAULTS) == (10, 5.0)
    # Scenario 3: 입력 검사(저장 전) — 범위 안이면 None, 밖이면 사람이 읽을 이유
    assert download_pacing.validate(10, 5) is None and download_pacing.validate(0, 0.5) is None and download_pacing.validate(200, 120) is None
    for cap, rest in ((-1, 5), (201, 5), (10, 0.4), (10, 121)):
        assert download_pacing.validate(cap, rest), (cap, rest)
    repository.set_setting(download_pacing.CAP_KEY, None); repository.set_setting(download_pacing.REST_KEY, None)
    print("1~3) 한도 규칙 OK")


async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 4: 처음엔 기본값(customized 거짓)
        r = (await c.get("/api/settings/download-pacing")).json()
        assert r["customized"] is False and r["default_max_new_episodes_per_title"] > 0 and r["default_batch_rest_minutes"] > 0
        default_cap, default_rest = r["max_new_episodes_per_title"], r["batch_rest_minutes"]
        # Scenario 5: 저장하면 바로 반영되고, 다시 읽으면 그 값이다
        r = (await c.post("/api/settings/download-pacing", json={"max_new_episodes_per_title": 20, "batch_rest_minutes": 1})).json()
        assert r["max_new_episodes_per_title"] == 20 and r["batch_rest_minutes"] == 1 and r["customized"] is True
        assert download_pacing.limits(DEFAULTS) == (20, 1.0)
        # Scenario 6: 잘못된 값은 400과 이유 — 저장되지 않는다
        for body in ({"max_new_episodes_per_title": -1, "batch_rest_minutes": 1}, {"max_new_episodes_per_title": 10, "batch_rest_minutes": 0.1},
                     {"max_new_episodes_per_title": 500, "batch_rest_minutes": 1}, {"max_new_episodes_per_title": 10, "batch_rest_minutes": 999}):
            resp = await c.post("/api/settings/download-pacing", json=body)
            assert resp.status_code == 400 and resp.json()["detail"], (body, resp.text)
        assert download_pacing.limits(DEFAULTS) == (20, 1.0)
        # Scenario 7: 기본값으로 되돌리기
        r = (await c.delete("/api/settings/download-pacing")).json()
        assert r["customized"] is False and (r["max_new_episodes_per_title"], r["batch_rest_minutes"]) == (default_cap, default_rest)
        assert download_pacing.limits(DEFAULTS) == (10, 5.0)
    print("4~7) 설정 API OK")


# ── 카카오: 저장한 값으로 실제 자동 다운로드가 돈다 ──
DL = tempfile.mkdtemp()
S = NS(request_timeout_seconds=5, delay_seconds=0, max_new_episodes_per_title=10, batch_rest_minutes=5.0, download_root=DL)
calls = []; STATE = {}
class FakeClient:
    cookies = {"x": "y"}
    async def check_login(self): return True
kp.client_from_saved_cookies = lambda session, timeout: FakeClient()
kp.persist_refreshed_cookies = lambda client: None
async def no_notify(*a, **k): return False
auth.notify_if_needed = no_notify
def E(n): return kp.Episode(product_id=n, title="t", number=n, subtitle=f"{n}화", is_free=True, accessible=True, page_count=1, hidden=False)
async def fake_run_download(client, *, series_id, title, download_root, max_episodes, on_progress=None, use_rental_tickets=False):
    calls.append(max_episodes)
    todo = [n for n in range(1, STATE["total"] + 1) if n not in STATE["have"]]
    plan = kp.DownloadPlan(mode="compare", to_download=[E(n) for n in todo])
    take = todo[:max_episodes]; STATE["have"].update(take)
    return kp.RunResult(take, [(n, f"{n}화") for n in take], None, plan, title="작품")
kp.run_download = fake_run_download


async def kakao_uses_saved_values():
    repository.upsert_new_kakao_webtoon(1, "작품", status=repository.STATUS_ACTIVE)
    job_status.start("download")
    # Scenario 8: 환경변수 기본값(10회차/5분) — 25회차는 10+10+5, 사이에 5분씩
    STATE.update({"total": 25, "have": set()}); calls.clear(); sleeps.clear()
    await scheduler._download_kakao_subscriptions(S, [])
    assert calls == [10, 10, 10] and [x for x in sleeps if x] == [300.0, 300.0], (calls, sleeps)
    # Scenario 9: 설정 화면에서 3회차/0.5분으로 바꾸면 재시작 없이 다음 실행부터 그대로 적용 — 7회차는 3+3+1, 사이에 30초씩
    repository.set_setting(download_pacing.CAP_KEY, "3"); repository.set_setting(download_pacing.REST_KEY, "0.5")
    STATE.update({"total": 7, "have": set()}); calls.clear(); sleeps.clear()
    await scheduler._download_kakao_subscriptions(S, [])
    assert calls == [3, 3, 3] and [x for x in sleeps if x] == [30.0, 30.0], (calls, sleeps)
    print("8~9) 카카오 자동 다운로드가 저장한 값을 쓴다 OK")


async def naver_uses_saved_values():
    # Scenario 10: 네이버 자동 다운로드도 같은 값을 쓴다(상한마다 쉬었다가 이어받기)
    done = []
    async def fake_single(**k): done.append(k["episode"].episode_no); return True, None
    scheduler.download_single_episode = fake_single
    scheduler.zip_episode_folders = lambda d: None
    download_roots.naver_root = lambda s: DL
    repository.upsert_new("1", "네이버 작품")
    plan = NS(info=NS(title_name="네이버 작품", webtoon_type="webtoon"), pending=[NS(episode_no=n, subtitle=f"{n}화") for n in range(1, 8)], cookies={}, webtoon_dir=Path(DL))
    NS_S = NS(delay_seconds=0, folder_zero_fill=4, image_zero_fill=4, max_concurrent_downloads=1, request_timeout_seconds=5, max_new_episodes_per_title=10, batch_rest_minutes=5.0, download_root=DL)
    sleeps.clear()
    await scheduler._download_pending_batches(None, NS_S, "1", plan, [])      # 저장한 값 3회차/0.5분이 그대로 남아 있다
    assert done == [1, 2, 3, 4, 5, 6, 7] and [x for x in sleeps if x] == [30.0, 30.0], (done, sleeps)
    repository.set_setting(download_pacing.CAP_KEY, None); repository.set_setting(download_pacing.REST_KEY, None)
    print("10) 네이버 자동 다운로드가 저장한 값을 쓴다 OK")

limits_rules()
asyncio.run(api())
asyncio.run(kakao_uses_saved_values())
asyncio.run(naver_uses_saved_values())
print("ALL OK")
