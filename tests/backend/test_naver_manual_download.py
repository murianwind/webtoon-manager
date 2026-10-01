"""네이버 수동 다운로드(download_selected) 특성 테스트 — 실패 조건, 선택한 회차만 번호순, 잠긴 회차 건너뜀, 실패해도 계속, 구독 중이면 마지막 회차 갱신."""
import asyncio
from types import SimpleNamespace as NS

from app import db, repository, manual_download as md, job_status, naver_api

db.get_connection()
import tempfile
DL = tempfile.mkdtemp()   # 실제로 폴더가 만들어지는 경로 — 시스템 루트(/dl)가 아니라 임시 폴더를 쓴다(일반 사용자 권한의 CI에서도 되도록)
S = NS(request_timeout_seconds=5, cookie_file_path=DL + "/cookies.json", folder_zero_fill=4, image_zero_fill=3, max_concurrent_downloads=2, download_root=DL)
EP = lambda n, locked=False: NS(episode_no=n, subtitle=f"{n}화", is_locked=locked)
state = {}
async def fake_info(session, title_id, timeout): return state["info"]
async def fake_all(session, title_id, cookies, timeout): state["cookies_used"] = cookies; return state["episodes"]
async def fake_single(**kw):
    state["attempts"].append(kw["episode"].episode_no); state["kw"] = kw
    return kw["episode"].episode_no not in state["fail"], None
async def fake_cover(session, d, info, t): state["cover"] += 1
naver_api.fetch_title_info = fake_info; naver_api.fetch_all_episodes = fake_all; naver_api.free_episodes_only = lambda eps: [e for e in eps if not e.is_locked]
md.download_single_episode = fake_single; md.zip_episode_folders = lambda d: state["zips"].append(str(d))
md.find_last_downloaded_episode_no = lambda d, eps: state["folder_last"]
md.get_adult_cookies = lambda path: state["adult_cookies"]
md.write_comicinfo_file = lambda d, info: state["xml"].append(info.title_name)
md.needs_comicinfo = lambda d: True; md.download_cover_image = fake_cover

def reset(**kw):
    state.update(info=NS(title_name="작품", is_adult=False, webtoon_type="WEBTOON"), episodes=[], fail=set(), attempts=[], zips=[], xml=[], cover=0, folder_last=0, adult_cookies={}, cookies_used=None, kw=None)
    state.update(kw); job_status.start("manual")
async def run(title_id, nos): await md.download_selected(title_id, nos, S); return job_status.snapshot()["manual"]
def logs(s): return "\n".join(s["log"])
def hist(tid): return sorted((r["episode_no"], r["status"]) for r in repository.list_episode_history_since("2000-01-01") if r["title_id"] == tid)

async def main():
    # ── 1. 시작할 수 없는 경우: 오류로 끝내고 아무것도 받지 않는다 ──
    reset(info=None); s = await run("1", [1]); assert s["status"] == "error" and "웹툰 정보를 가져오지 못했습니다" in logs(s) and state["xml"] == []
    reset(info=NS(title_name="성인작", is_adult=True, webtoon_type="WEBTOON"), episodes=[EP(1)]); s = await run("1", [1])
    assert s["status"] == "error" and "성인 웹툰 인증 쿠키가 없습니다" in logs(s) and state["attempts"] == []
    reset(episodes=[EP(1), EP(2)]); s = await run("1", [9]); assert s["status"] == "error" and "선택한 회차를 목록에서 찾지 못했습니다" in logs(s) and state["xml"] == []
    print("1) 시작 못 하는 경우 OK")

    # ── 2. 선택한 회차만 번호순으로: 잠긴 회차는 건너뛰고, 정보 파일/커버 갱신, 성공 이력, 압축 ──
    reset(episodes=[EP(n, locked=(n == 3)) for n in range(1, 6)]); s = await run("1", [5, 3, 1])
    assert state["attempts"] == [1, 5] and s["status"] == "success" and state["xml"] == ["작품"] and state["cover"] == 1 and len(state["zips"]) == 2
    assert "3화: 유료/잠김 — 건너뜀" in logs(s) and "3개 회차 다운로드 시작" in logs(s) and "✅ 1화 \"1화\" 완료" in logs(s) and logs(s).rstrip().endswith("수동 다운로드 종료")
    assert hist("1") == [(1, "success"), (5, "success")]
    kw = state["kw"]; assert kw["title_id"] == "1" and kw["folder_zero_fill"] == 4 and kw["download_root"] == DL and kw["cookies"] == {}
    print("2) 선택 회차 다운로드 OK (번호순, 잠김 건너뜀, 이력, 압축)")

    # ── 3. 성인 웹툰은 쿠키로 받는다 ──
    reset(info=NS(title_name="성인작", is_adult=True, webtoon_type="WEBTOON"), episodes=[EP(1)], adult_cookies={"NID": "x"}); s = await run("2", [1])
    assert state["cookies_used"] == {"NID": "x"} and state["attempts"] == [1] and s["status"] == "success"
    print("3) 성인 웹툰 OK")

    # ── 4. 실패해도 나머지는 계속 받고(자동 다운로드와 다름), 끝난 상태는 오류 ──
    reset(episodes=[EP(n) for n in range(1, 5)], fail={2}); s = await run("3", [1, 2, 3, 4])
    assert state["attempts"] == [1, 2, 3, 4] and s["status"] == "error" and "❌ 2화 \"2화\" 다운로드 실패" in logs(s)
    assert hist("3") == [(1, "success"), (2, "failed"), (3, "success"), (4, "success")] and len(state["zips"]) == 3
    print("4) 실패해도 계속 OK")

    # ── 5. 구독 중인 작품이면 마지막 회차를 폴더 기준으로 갱신(앞설 때만), 구독 안 한 작품이면 건드리지 않는다 ──
    repository.upsert_new(title_id="10", title="구독작"); repository.update_last_downloaded_no("10", 2)
    reset(episodes=[EP(n) for n in range(1, 6)], folder_last=4); await run("10", [3, 4]); assert repository.get("10").last_downloaded_no == 4
    reset(episodes=[EP(n) for n in range(1, 6)], folder_last=1); await run("10", [1]); assert repository.get("10").last_downloaded_no == 4          # 폴더 기준이 더 작으면 내리지 않는다
    reset(episodes=[EP(1)], folder_last=1); s = await run("404", [1]); assert s["status"] == "success" and repository.get("404") is None          # 구독 안 한 작품
    print("5) 마지막 회차 갱신 OK")
asyncio.run(main())
print("\n전부 통과")
