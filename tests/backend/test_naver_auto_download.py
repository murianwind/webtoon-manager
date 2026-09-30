"""네이버 자동 다운로드(한 작품) 특성 테스트 — 건너뛰는 조건, 성인 쿠키, 폴더 기준 갱신, 순서/이력, 실패 시 중단, 배치 휴식."""
import asyncio
from types import SimpleNamespace as NS

from app import db, repository, scheduler, job_status, comicinfo, naver_api, cookie_health
db.get_connection()

S = NS(request_timeout_seconds=5, cookie_file_path="/none", folder_zero_fill=4, image_zero_fill=3, max_concurrent_downloads=2, delay_seconds=0,
       max_new_episodes_per_title=2, batch_rest_minutes=7, download_root="/dl")
EP = lambda n: NS(episode_no=n, subtitle=f"{n}화")
state = {}
async def fake_info(session, title_id, timeout): return state["info"]
async def fake_all(session, title_id, cookies, timeout): state["cookies_used"] = cookies; return state["episodes"]
async def fake_single(**kw):
    state["attempts"].append(kw["episode"].episode_no); state["download_kw"] = kw
    return kw["episode"].episode_no not in state["fail"], None
async def fake_cover(session, d, info, t): state["cover"] += 1
sleeps = []
async def fake_sleep(x): sleeps.append(x)
naver_api.fetch_title_info = fake_info; naver_api.fetch_all_episodes = fake_all; naver_api.free_episodes_only = lambda eps: list(eps)
scheduler.download_single_episode = fake_single; scheduler.zip_episode_folders = lambda d: state["zips"].append(str(d))
scheduler.find_last_downloaded_episode_no = lambda d, eps: state["folder_last"]
scheduler.get_adult_cookies = lambda path: state["adult_cookies"]
comicinfo.write_comicinfo_file = lambda d, info: state["xml"].append(info.title_name)
comicinfo.needs_comicinfo = lambda d: True; comicinfo.download_cover_image = fake_cover
scheduler.asyncio.sleep = fake_sleep

def reset(**kw):
    state.update(info=NS(title_name="작품", is_adult=False, webtoon_type="WEBTOON"), episodes=[], fail=set(), attempts=[], zips=[], xml=[], cover=0, folder_last=0, adult_cookies={}, cookies_used=None, download_kw=None)
    state.update(kw); sleeps.clear(); job_status.start("download")
async def run(title_id="1", tracker=None, failures=None):
    failures = [] if failures is None else failures
    await scheduler._download_new_episodes_for_one(None, S, title_id, tracker or cookie_health.AdultFetchTracker(), failures); return failures
def rests(): return [x for x in sleeps if x]     # 회차 사이 대기(0초)는 제외하고 배치 휴식만
def clear_history():
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
def logs(): return "\n".join(job_status.snapshot()["download"]["log"])
def history(): return [(r["episode_no"], r["status"]) for r in repository.list_episode_history_since("2000-01-01") if r["title_id"] == "1"]

async def main():
    repository.upsert_new(title_id="1", title="작품")
    get = lambda: repository.get("1")

    # ── 1. 대상이 아니면 아무것도 안 한다 ──
    reset(); await run("999"); assert state["attempts"] == [] and state["xml"] == []                               # 모르는 작품
    repository.set_status("1", repository.STATUS_UNSUBSCRIBED); reset(episodes=[EP(1)]); await run(); assert state["attempts"] == [] and state["xml"] == []   # 구독 중이 아님
    repository.set_status("1", repository.STATUS_ACTIVE)
    reset(info=None); await run(); assert "정보 조회 실패, 건너뜀" in logs() and state["xml"] == []                   # 정보 조회 실패
    print("1) 건너뛰는 조건 OK")

    # ── 2. 성인 웹툰: 쿠키 없으면 건너뜀 / 있으면 쿠키로 받고 쿠키 만료 감지용으로 회차 수를 기록 ──
    reset(info=NS(title_name="작품", is_adult=True, webtoon_type="WEBTOON"), episodes=[EP(1)]); await run()
    assert "성인 웹툰 인증 쿠키 없음, 건너뜀" in logs() and state["attempts"] == [] and get().is_adult is True
    tracker = cookie_health.AdultFetchTracker(); recorded = []; tracker.record = lambda n: recorded.append(n)
    reset(info=NS(title_name="작품", is_adult=True, webtoon_type="WEBTOON"), episodes=[EP(1), EP(2)], adult_cookies={"NID": "x"}); await run(tracker=tracker)
    assert recorded == [2] and state["cookies_used"] == {"NID": "x"} and state["attempts"] == [1, 2]
    repository.update_last_downloaded_no("1", 0); repository.update_is_adult("1", False)
    print("2) 성인 웹툰 OK")

    # ── 3. 받을 게 없으면: 폴더 기준으로 마지막 회차 갱신 + 정보 파일/표지만 갱신 ──
    repository.update_last_downloaded_no("1", 1)
    reset(episodes=[EP(1), EP(2), EP(3)], folder_last=3); await run()
    assert state["attempts"] == [] and get().last_downloaded_no == 3 and get().latest_episode_no == 3                # DB(1)보다 폴더(3)가 앞서면 폴더 기준으로
    assert "폴더 확인 결과 3화까지 완료 (DB 기록 1화에서 갱신)" in logs() and state["xml"] == ["작품"] and state["cover"] == 1 and "새 회차" not in logs()
    print("3) 받을 게 없을 때 OK")

    # ── 4. 새 회차: 번호순으로 받고 압축 + 이력 + 마지막 회차 갱신 (상한 2개씩, 배치 사이 휴식) ──
    repository.update_last_downloaded_no("1", 0); clear_history()
    reset(episodes=[EP(n) for n in range(1, 6)]); await run()
    assert state["attempts"] == [1, 2, 3, 4, 5] and len(state["zips"]) == 5 and get().last_downloaded_no == 5
    assert sorted(history()) == [(n, "success") for n in range(1, 6)]
    assert rests() == [7 * 60, 7 * 60] and "새 회차 5개 다운로드 시작" in logs() and "2화 받음, 남은 3화는 7분 쉬었다가 이어받기" in logs()   # 2+2+1 → 배치 사이 휴식 2번
    kw = state["download_kw"]; assert kw["title_id"] == "1" and kw["folder_zero_fill"] == 4 and kw["image_zero_fill"] == 3 and kw["max_concurrent_downloads"] == 2 and kw["download_root"] == "/dl"
    print("4) 새 회차 다운로드 OK (순서, 압축, 이력, 배치 휴식)")

    # ── 5. 실패하면 그 작품은 거기서 중단: 실패 이력/알림 목록에 기록, 남은 회차는 안 받음, 마지막 회차는 성공한 데까지 ──
    repository.update_last_downloaded_no("1", 0)
    clear_history()
    reset(episodes=[EP(n) for n in range(1, 6)], fail={3}); failures = await run()
    assert state["attempts"] == [1, 2, 3] and get().last_downloaded_no == 2 and sorted(history()) == [(1, "success"), (2, "success"), (3, "failed")]
    assert failures == [{"title_name": "작품", "episode_no": 3, "subtitle": "3화"}] and "3화 다운로드 실패 — 다음 실행에서 재시도" in logs() and rests() == [7 * 60]   # 1·2화(첫 배치) 뒤 휴식은 했고, 실패한 뒤에는 더 쉬지도 받지도 않는다
    print("5) 실패 시 중단 OK")

    # ── 6. 상한이 0이면 한 번에 전부(휴식 없음) ──
    S.max_new_episodes_per_title = 0; repository.update_last_downloaded_no("1", 0)
    reset(episodes=[EP(n) for n in range(1, 4)]); await run(); assert state["attempts"] == [1, 2, 3] and rests() == []
    print("6) 상한 없음 OK")
asyncio.run(main())
print("\n전부 통과")
