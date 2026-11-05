"""웹툰 전체목록 알림의 "신규 웹툰" 섹션과 "새 에피소드" 섹션의 대상 규칙(scheduler/report_sections.py).

신규 웹툰 = 사이트에서 "신작" 표시가 있는 미구독 작품 중, 구독/구독해제/제외됨이 아니고(기록이 없거나 "목록" 상태), 매일+가 아니고, **등록한(사용 중인) 작가의 작품이 아니며**(그건 작가 알림이 따로 알린다), 아직 신규로 알린 적 없는 것.
한 번 알리면 기억해 둬서(report_seen) 다시 신규로 알리지 않는다. 새 에피소드 섹션은 신규가 아닌 작품이 대상이고, 신규로 알린 뒤에는 (신작 표시가 남아 있어도) 새 회차가 나올 때마다 대상이 된다.
목록 제외를 안 하면 이후 새 회차는 새 에피소드로 나가고, 제외했다가 "목록으로" 되돌려도(기록이 지워져도) 다시 새 에피소드 대상이 된다 — 실제 화면이 쓰는 API(제외/목록으로)로 확인한다.
"""
import asyncio

import httpx

from app import daily_plus, db, kakao_api, naver_api, report_seen, repository
from app.config import get_settings
from app.models import NaverListItem
db.get_connection()
import app.main as m
from app.scheduler import report_sections as rs

_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
settings = get_settings()
EX, UN, AC = repository.STATUS_EXCLUDED, repository.STATUS_UNSUBSCRIBED, repository.STATUS_ACTIVE

# ── 가짜 사이트 ──
NAVER, KAKAO, HIDDEN = [], [], set()
def n(tid, name, *, new=True, up=True, authors="작가", adult=False): return NaverListItem(title_id=str(tid), title_name=name, thumbnail_url="", weekdays=["mon"], author_summary=authors, is_new=new, has_update=up, is_adult=adult)
def k(tid, name, *, new=True, up=True, authors=("작가",)): return {"title_id": tid, "title_name": name, "is_adult": False, "author_names": list(authors), "has_update": up, "is_new": new, "is_paused": False, "is_finished": False, "thumbnail_url": "", "weekdays": ["mon"]}
async def fake_list(session, timeout): return list(NAVER)
async def fake_hidden(timeout, session): return set(HIDDEN)
async def fake_episode(session, title_id, timeout, cookies=None): return 7
async def fake_catalog(session, timeout, use_cache=True): return list(KAKAO)
async def fake_kakao_url(session, title_id, timeout, cookies=None): return f"https://page.kakao.com/content/{title_id}/viewer/9"
naver_api.fetch_full_webtoon_list, daily_plus.current_ids, naver_api.fetch_latest_episode_no = fake_list, fake_hidden, fake_episode
kakao_api.fetch_weekday_catalog, kakao_api.fetch_latest_episode_url = fake_catalog, fake_kakao_url
ids = lambda rows: [str(r[0]) for r in rows]
new_titles = lambda force=False: asyncio.run(rs.collect_new_title_section(None, settings, force_test=force))
episodes = lambda force=False: asyncio.run(rs.collect_new_episode_sections(None, settings, force_test=force))
reset_seen = lambda: repository.set_setting(report_seen.SETTING_KEY, None)
repository.set_setting("kakao_webtoons_enabled", "1")

# Scenario 1: 네이버 신규 웹툰 대상 — 신작 표시 + 미구독(기록 없음/"목록") + 매일+ 아님 + 등록한 작가 아님 + 처음 알림
repository.upsert_watched_author("100", "홍길동", True, "naver"); repository.upsert_watched_author("101", "꺼둔작가", False, "naver")
NAVER[:] = [n(1, "기록없음"), n(2, "제외됨"), n(3, "구독중"), n(4, "구독해제"), n(5, "목록상태"), n(6, "매일플러스"), n(7, "등록작가", authors="홍길동"), n(8, "꺼둔작가의작품", authors="꺼둔작가"),
            n(9, "여러작가", authors="김작, 홍길동 / 이그림"), n(10, "신작아님", new=False), n(11, "이미알림")]
for tid, status in ((2, EX), (3, AC), (4, UN), (5, repository.STATUS_UNREGISTERED)): repository.upsert_new(str(tid), f"작품{tid}"); repository.set_status(str(tid), status)
HIDDEN.add("6"); report_seen.remember_new([("11", "이미알림")], [])
naver_new, kakao_new = new_titles(); assert ids(naver_new) == ["1", "5", "8"], ids(naver_new)
assert naver_new[0] == ("1", "기록없음"), "(작품 번호, 제목)"
print("1) 네이버 신규 대상 OK")

# Scenario 2: 카카오도 같은 규칙 — 구독 중/구독해제/제외됨 제외, 등록 작가(이름) 제외, 이미 알림 제외
repository.upsert_watched_author("카작가", "카작가", True, "kakao")
KAKAO[:] = [k(501, "카-기록없음"), k(502, "카-제외됨"), k(503, "카-구독중"), k(504, "카-구독해제"), k(505, "카-등록작가", authors=("카작가", "다른")), k(506, "카-신작아님", new=False), k(507, "카-이미알림")]
for tid, status in ((502, EX), (503, AC), (504, UN)): repository.upsert_new_kakao_webtoon(tid, f"카{tid}", status=status)
report_seen.remember_new([], [(507, "카-이미알림")])
naver_new, kakao_new = new_titles(); assert ids(kakao_new) == ["501"] and kakao_new[0] == (501, "카-기록없음"), kakao_new
print("2) 카카오 신규 대상 OK")

# Scenario 3: 한 번 알리면 다시 신규로 알리지 않는다(알렸다고 기록한 뒤) — 테스트 발송(force_test)은 알린 것도 보여 준다(등록 작가 제외 규칙은 그대로)
before = new_titles(); report_seen.remember_new(*before)
again = new_titles(); assert again == ([], []), again
forced = new_titles(force=True); assert ids(forced[0]) == ["1", "5", "8", "11"] and ids(forced[1]) == ["501", "507"], forced
print("3) 한 번만 알림 OK")

# Scenario 4: 새 에피소드 섹션은 신규가 아닌 작품이 대상 — 신작 표시가 있는데 아직 신규로 알리지 않은 작품은 빠지고(신규 섹션에 나간다), 신규로 알린 뒤에는 새 회차가 나올 때 대상이 된다
reset_seen(); reset = lambda: None
NAVER[:] = [n(21, "신규+UP"), n(22, "일반+UP", new=False), n(23, "신규+UP+등록작가", authors="홍길동")]
naver_eps, kakao_eps = episodes(); assert ids(naver_eps) == ["22"], ids(naver_eps)      # 21은 신규 섹션으로, 23(등록 작가의 신규)은 작가 알림이 알리므로 어느 쪽에도 안 나간다
naver_new, _ = new_titles(); assert ids(naver_new) == ["21"]
report_seen.remember_new(naver_new, [])
naver_eps, _ = episodes(); assert ids(naver_eps) == ["21", "22"], "신규로 알린 뒤에는 새 에피소드 대상(신작 표시가 남아 있어도)"
assert ids(episodes(force=True)[0]) == ["21", "22"]
print("4) 새 에피소드는 신규가 아닌 작품 대상 OK")

# Scenario 4b: 카카오도 같다 — 신작 표시가 있고 아직 신규로 알리지 않은 작품은 새 에피소드에서 빠지고, 알린 뒤에는 들어간다
reset_seen(); NAVER[:] = []; KAKAO[:] = [k(701, "카-신규+UP"), k(702, "카-일반+UP", new=False)]
assert ids(episodes()[1]) == ["702"], ids(episodes()[1])
report_seen.remember_new([], [(701, "카-신규+UP")]); assert ids(episodes()[1]) == ["701", "702"]
print("4b) 카카오 새 에피소드에서 신규 빼기 OK")

# Scenario 4c: 섹션 옵션이 꺼져 있으면 신규 웹툰도 안 나간다 / 한 번에 알리는 신규는 _NEW_TITLE_LIMIT개까지(앞에서부터)
reset_seen(); NAVER[:] = [n(800 + i, f"대량신규{i}") for i in range(rs._NEW_TITLE_LIMIT + 10)]; KAKAO[:] = [k(900 + i, f"카-대량{i}") for i in range(rs._NEW_TITLE_LIMIT + 10)]
naver_new, kakao_new = new_titles(); assert len(naver_new) == rs._NEW_TITLE_LIMIT == len(kakao_new) and naver_new[0][0] == "800" and naver_new[-1][0] == str(800 + rs._NEW_TITLE_LIMIT - 1), (len(naver_new), len(kakao_new))
repository.set_setting("report_unregistered_new_episodes_enabled", "0"); assert new_titles() == ([], []), "옵션이 꺼져 있으면 신규 웹툰도 안 나간다"
repository.set_setting("report_unregistered_new_episodes_enabled", "1")
print("4c) 옵션 꺼짐/개수 제한 OK")

# Scenario 5 (요청: 신규 웹툰을 목록 제외했다가 다시 전체목록으로 옮기면 새 에피소드에 포함되는지) — 실제 화면 API로
async def flow():
    anew = lambda: rs.collect_new_title_section(None, settings, force_test=False)                                          # 이미 이벤트 루프 안이라 await로 부른다
    aeps = lambda: rs.collect_new_episode_sections(None, settings, force_test=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        reset_seen(); NAVER[:] = [n(31, "되돌릴작품")]
        new, _ = (await anew()); assert ids(new) == ["31"]; report_seen.remember_new(new, [])                                  # ① 신규 웹툰으로 알림
        assert ids((await aeps())[0]) == ["31"], "목록 제외를 안 하면 이후 새 에피소드로 나간다"
        assert (await c.post("/api/naver-list/31/exclude", json={"title": "되돌릴작품"})).status_code == 200                  # ② 목록 제외(구독한 적 없음)
        assert ids((await aeps())[0]) == [] and ids((await anew())[0]) == [], "제외된 작품은 어느 섹션에도 안 나간다"
        assert (await c.delete("/api/webtoons/31")).status_code == 200                                                       # ③ 제외됨에서 "목록으로"(구독 이력이 없으면 완전 삭제)
        assert repository.get("31") is None
        assert ids((await aeps())[0]) == ["31"], "다시 전체목록으로 돌아오면 새 에피소드에 포함된다(신작 표시가 남아 있어도)"
        assert ids((await anew())[0]) == [], "신규로 또 알리지는 않는다"
        # 구독한 적 있는 작품은 "목록으로"가 삭제가 아니라 목록 상태로 옮긴다
        repository.upsert_new("32", "구독했던작품"); repository.set_status("32", AC); repository.set_status("32", EX); NAVER[:] = [n(32, "구독했던작품")]
        report_seen.remember_new([("32", "구독했던작품")], [])
        assert ids((await aeps())[0]) == [], "제외됨이면 안 나간다"
        assert (await c.post("/api/webtoons/32/unregister")).status_code == 200 and repository.get("32").status == repository.STATUS_UNREGISTERED
        assert ids((await aeps())[0]) == ["32"], "목록 상태로 돌아오면 새 에피소드에 포함된다"
        # 카카오도 같다
        KAKAO[:] = [k(601, "카-되돌릴작품")]; report_seen.remember_new([], [(601, "카-되돌릴작품")])
        assert ids((await aeps())[1]) == ["601"]
        assert (await c.post("/api/kakao-webtoons/601/exclude", json={"title": "카-되돌릴작품"})).status_code == 200 and ids((await aeps())[1]) == []
        assert (await c.delete("/api/kakao-webtoons/601")).status_code == 200 and ids((await aeps())[1]) == ["601"], "카카오: 목록으로 돌아오면 새 에피소드에 포함"
asyncio.run(flow())
print("5) 제외했다가 목록으로 되돌리면 새 에피소드에 포함 OK")
print("ALL OK")
