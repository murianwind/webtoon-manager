"""리포트의 "웹툰 전체목록 중 새 에피소드" — 성인 작품(네이버 18세, 카카오 19세)도 빠지지 않게: 저장된 성인/로그인 쿠키로 최신 회차를 조회하고,
일반 작품은 쿠키 없이 조회한다. 쿠키가 없거나 만료돼서 확인하지 못한 성인 작품은 조용히 넘기지 않고 리포트 실행 로그에 남긴다. (네이버/카카오 같은 규칙)"""
import asyncio, json, time
from types import SimpleNamespace as NS

from app import db, job_status, kakao_api, kakao_page_auth as auth, naver_api, scheduler
from app.config import get_settings
db.get_connection()

ST = get_settings()
S = NS(request_timeout_seconds=5, artist_scan_concurrency=3, delay_seconds=0, cookie_file_path=ST.cookie_file_path)
def logs(): return "\n".join(job_status.snapshot()["report"]["log"])

# ── 네이버: 성인 작품은 쿠키 없이는 회차 목록이 비어서 온다(실제 네이버 동작 — 성인 쿠키 만료 감지도 이 전제를 쓴다) ──
NAVER_ITEMS = [NS(title_id="1", title_name="일반작", has_update=True, is_adult=False), NS(title_id="2", title_name="초임계지옥", has_update=True, is_adult=True)]
naver_calls = []
async def fake_list(session, timeout): return list(NAVER_ITEMS)
async def fake_latest(session, title_id, timeout, cookies=None):
    naver_calls.append((title_id, cookies))
    adult = title_id == "2"
    return (6 if cookies and cookies.get("NID_AUT") else None) if adult else 10
naver_api.fetch_full_webtoon_list = fake_list; naver_api.fetch_latest_episode_no = fake_latest

# ── 카카오: 같은 규칙(19세는 로그인 쿠키로 조회) ──
KAKAO_ITEMS = [{"title_id": 11, "title_name": "카카오일반", "has_update": True, "is_adult": False}, {"title_id": 12, "title_name": "카카오19금", "has_update": True, "is_adult": True}]
kakao_calls = []
async def fake_catalog(session, timeout, use_cache=True): return [dict(i) for i in KAKAO_ITEMS]
async def fake_kakao_latest(session, series_id, timeout, cookies=None):
    kakao_calls.append((series_id, cookies))
    adult = series_id == 12
    return (f"https://page.kakao.com/content/{series_id}/viewer/1" if cookies else None) if adult else f"https://page.kakao.com/content/{series_id}/viewer/1"
kakao_api.fetch_weekday_catalog = fake_catalog; kakao_api.fetch_latest_episode_url = fake_kakao_latest

def save_naver_adult_cookie():
    with open(ST.cookie_file_path, "w", encoding="utf-8") as f:
        json.dump({"cookies": [{"domain": ".naver.com", "name": "NID_AUT", "value": "aut", "path": "/"}, {"domain": ".naver.com", "name": "NID_SES", "value": "ses", "path": "/"}]}, f)   # 실제 쿠키 파일 형식
def save_kakao_cookie():
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": f"v-{n}-0123456789", "path": "/", "expirationDate": time.time() + 9e5} for n in auth.REQUIRED_COOKIES])))

async def main():
    # 1) 쿠키 없음: 일반 작품만 나오고, 성인 작품은 "확인하지 못함"으로 로그에 남는다
    job_status.start("report")
    got_n = await scheduler._collect_unregistered_new_episodes(None, S)
    got_k = await scheduler._collect_kakao_new_episodes(None, S)
    assert [t for t, *_ in got_n] == ["1"] and [t for t, *_ in got_k] == [11], (got_n, got_k)
    assert "네이버 성인 작품 1개" in logs() and "초임계지옥" in logs() and "카카오 성인 작품 1개" in logs() and "카카오19금" in logs(), logs()
    print("1) 쿠키 없음: 성인 작품은 로그로 알림 OK")

    # 2) 쿠키 있음: 성인 작품도 나오고, 성인 작품 조회에만 쿠키를 붙인다(일반 작품은 쿠키 없이)
    save_naver_adult_cookie(); save_kakao_cookie(); naver_calls.clear(); kakao_calls.clear(); job_status.start("report")
    got_n = await scheduler._collect_unregistered_new_episodes(None, S)
    got_k = await scheduler._collect_kakao_new_episodes(None, S)
    assert sorted(t for t, *_ in got_n) == ["1", "2"] and dict((t, n) for t, _, n in got_n)["2"] == 6, got_n
    assert sorted(t for t, *_ in got_k) == [11, 12], got_k
    calls_n = dict(naver_calls); calls_k = dict(kakao_calls)
    assert calls_n["1"] is None and calls_n["2"] == {"NID_AUT": "aut", "NID_SES": "ses"}, calls_n
    assert calls_k[11] is None and set(calls_k[12]) == set(auth.REQUIRED_COOKIES), calls_k
    assert "성인 작품" not in logs(), logs()
    print("2) 쿠키 있음: 성인 작품 포함 + 성인 조회에만 쿠키 OK")
asyncio.run(main())
print("\n전부 통과")
