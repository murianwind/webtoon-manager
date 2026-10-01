"""카카오 자동 다운로드의 같은 실행 내 이어받기 — 네이버와 같은 설정(작품당 상한, 배치 사이 쉬는 시간)으로 상한에 걸리면 쉬었다가 이어 받는다."""
import asyncio
from types import SimpleNamespace as NS

from app import db, repository, scheduler, job_status, kakao_page_download as kp, kakao_page_auth as auth
db.get_connection()

import tempfile
DL = tempfile.mkdtemp()   # 실제로 폴더가 만들어지는 경로 — 시스템 루트(/dl)가 아니라 임시 폴더를 쓴다(일반 사용자 권한의 CI에서도 되도록)
S = NS(request_timeout_seconds=5, delay_seconds=0, max_new_episodes_per_title=10, batch_rest_minutes=5.0, download_root=DL)
sleeps = []; calls = []; persisted = []; STATE = {}
async def fake_sleep(x): sleeps.append(x)
scheduler.asyncio.sleep = fake_sleep

class FakeClient:
    cookies = {"x": "y"}
    async def check_login(self): return True
kp.client_from_saved_cookies = lambda session, timeout: FakeClient()
kp.persist_refreshed_cookies = lambda client: persisted.append(1)
async def no_notify(*a, **k): return False
auth.notify_if_needed = no_notify

def E(n, acc=True): return kp.Episode(product_id=n, title="t", number=n, subtitle=f"{n}화", is_free=acc, accessible=acc, page_count=1, hidden=False)
async def fake_run_download(client, *, series_id, title, download_root, max_episodes, on_progress=None):
    """실제 run_download처럼 폴더(STATE) 기준으로 받을 회차를 계산해서 최대 max_episodes개 받은 것으로 친다."""
    st = STATE[series_id]; calls.append((series_id, max_episodes))
    todo = [n for n in range(1, st["total"] + 1) if n not in st["have"]]
    plan = kp.DownloadPlan(mode="compare", to_download=[E(n) for n in todo], locked=[E(n, False) for n in st.get("locked", [])])
    if st.get("fail_at") in todo[:max_episodes]:
        take = [n for n in todo if n < st["fail_at"]][:max_episodes]; st["have"].update(take)
        return kp.RunResult(take, [(n, f"{n}화") for n in take], st["fail_at"], plan, title=st["title"])
    take = todo[:max_episodes]; st["have"].update(take); ticket = None
    if st.get("ticket") and len(take) < max_episodes and plan.locked: ticket = plan.locked[0].number; take = take + [ticket]
    return kp.RunResult(take, [(n, f"{n}화") for n in take], None, plan, title=st["title"], ticket_used=ticket)
kp.run_download = fake_run_download
def reset(): calls.clear(); sleeps.clear(); persisted.clear(); job_status.start("download")
def rests(): return [x for x in sleeps if x]
def hist(sid): return sorted(r["episode_no"] for r in repository.list_episode_history_since("2000-01-01") if r["title_id"] == str(sid) and r["status"] == "success")
def clear_hist():
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
def logs(): return "\n".join(job_status.snapshot()["download"]["log"])

async def main():
    for sid, title in ((1, "많은작품"), (2, "실패작품"), (3, "기다무작품"), (4, "적은작품")): repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)

    # ── 1. 상한(10)을 넘는 작품은 쉬었다가 같은 실행 안에서 끝까지 받는다 ──
    STATE.update({1: {"title": "많은작품", "total": 25, "have": set()}}); only = lambda ids: [repository.set_kakao_webtoon_status(i, repository.STATUS_ACTIVE if i in ids else repository.STATUS_UNSUBSCRIBED) for i in (1, 2, 3, 4)]   # 지정한 작품만 구독 상태로
    only([1]); reset(); failures = []
    await scheduler._download_kakao_subscriptions(S, failures)
    assert [c[1] for c in calls] == [10, 10, 10] and STATE[1]["have"] == set(range(1, 26)) and hist(1) == list(range(1, 26)) and failures == []      # 10 + 10 + 5, 25개 모두
    assert rests() == [5 * 60, 5 * 60] and "남은" in logs() and "쉬었다가" in logs(), (rests(), logs()[-300:])                                      # 배치 사이에만 5분씩 쉬고, 마지막 뒤에는 안 쉼
    reset(); await scheduler._download_kakao_subscriptions(S, []); assert len(calls) == 1 and rests() == []                                          # 받을 게 없으면 한 번 확인하고 끝
    print("1) 상한 넘는 작품 이어받기 OK (10+10+5, 배치 사이 5분)")

    # ── 2. 상한이 딱 맞아떨어지는 경우: 다 받았으면 더 돌지 않는다 / 상한 0이면 한 번에 전부 ──
    STATE[1] = {"title": "많은작품", "total": 20, "have": set()}; reset(); await scheduler._download_kakao_subscriptions(S, [])
    assert len(calls) == 2 and rests() == [300] and STATE[1]["have"] == set(range(1, 21))                                                              # 10 + 10 — 둘째 배치에서 다 받았으면 더 돌지 않는다
    STATE[1] = {"title": "많은작품", "total": 25, "have": set()}; clear_hist(); S.max_new_episodes_per_title = 0; reset()
    await scheduler._download_kakao_subscriptions(S, []); assert len(calls) == 1 and rests() == [] and STATE[1]["have"] == set(range(1, 26)); S.max_new_episodes_per_title = 10
    print("2) 맞아떨어짐/상한 없음 OK")

    # ── 3. 중간에 실패하면 그 작품은 거기서 멈춘다(더 안 쉬고 더 안 받음), 실패는 기록 ──
    clear_hist(); STATE.clear(); STATE[2] = {"title": "실패작품", "total": 30, "have": set(), "fail_at": 14}; only([2]); reset(); failures = []
    await scheduler._download_kakao_subscriptions(S, failures)
    assert STATE[2]["have"] == set(range(1, 14)) and len(calls) == 2 and rests() == [300] and failures == [{"title_name": "실패작품", "episode_no": 14, "subtitle": "이미지 받기 실패"}]
    assert 14 not in hist(2) and any(r["status"] == "failed" and r["episode_no"] == 14 for r in repository.list_episode_history_since("2000-01-01"))
    print("3) 실패 시 중단 OK")

    # ── 4. 기다무로 연 회차는 "받을 회차" 남은 수 계산에서 빠진다(받을 게 남은 것으로 오해하지 않음) ──
    clear_hist(); STATE.clear(); STATE[3] = {"title": "기다무작품", "total": 6, "have": set(), "locked": [7, 8], "ticket": True}; only([3]); reset()
    await scheduler._download_kakao_subscriptions(S, []); assert len(calls) == 1 and rests() == [] and hist(3) == [1, 2, 3, 4, 5, 6, 7]
    print("4) 기다무 회차 계산 OK")

    # ── 5. 여러 작품: 작품마다 따로 이어받고, 쿠키는 작품마다 저장 ──
    clear_hist(); STATE.clear(); STATE.update({1: {"title": "많은작품", "total": 12, "have": set()}, 4: {"title": "적은작품", "total": 3, "have": set()}}); only([1, 4]); reset()
    await scheduler._download_kakao_subscriptions(S, []); assert STATE[1]["have"] == set(range(1, 13)) and STATE[4]["have"] == {1, 2, 3} and len(persisted) >= 2
    print("5) 여러 작품 OK")

    # ── 6. 안전장치: 계속 "남았다"고만 하는 비정상 응답이어도 무한 반복하지 않는다 ──
    async def stuck(client, *, series_id, title, download_root, max_episodes, on_progress=None):
        calls.append((series_id, max_episodes)); plan = kp.DownloadPlan(mode="compare", to_download=[E(1), E(2)])
        return kp.RunResult([1], [(1, "1화")], None, plan, title="무한")
    kp.run_download = stuck; only([4]); reset(); await scheduler._download_kakao_subscriptions(S, [])
    assert 2 <= len(calls) <= scheduler._KAKAO_MAX_BATCHES_PER_TITLE and "너무 많아" in logs()
    print("6) 무한 반복 방지 OK")
asyncio.run(main())
print("\n전부 통과")
