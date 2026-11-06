"""카카오 웹툰 중 제목에 "단행본"이 들어간 것은 어디에도 나오지 않는다 — 전체목록, 제외됨/구독해제, 수동 다운로드(검색/분석/받기), 작가 신작 구독(작가 검색), 신작 알림(새 에피소드/신규 웹툰), 새 에피소드 감시, 빠른 상태 스캔.
거르는 곳은 두 겹이다: ① 카카오에서 받아오는 곳(요일 목록 카드, 검색 카드)에서 처음부터 빼고(캐시에 예전부터 남은 것도 읽을 때 뺀다) ② 이미 DB에 있는 기록(예전에 등록/제외했던 것)이나 직접 번호로 여는 경로는 쓰는 곳에서 뺀다.
기준은 제목에 "단행본"이라는 글자가 그대로 들어 있는지다(공백을 끼운 "단 행 본"이나 "단행"만 있는 것은 아니다)."""
import asyncio
import tempfile
from pathlib import Path

import httpx

from app import db, kakao_api, kakao_catalog, kakao_cover, kakao_page_download as kp, new_episode_watch, quick_scan, repository
db.get_connection()
import app.main as m
from app.config import get_settings
from fake_kakao_rental_site import JPG, SITE, FakeSession, Site, item
import aiohttp

_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
aiohttp.ClientSession = FakeSession
EX, UN, AC = repository.STATUS_EXCLUDED, repository.STATUS_UNSUBSCRIBED, repository.STATUS_ACTIVE

try:
    from app import kakao_filters as kf
except ImportError:
    kf = None

# Scenario 1: 판정 — 제목에 "단행본"이 그대로 들어 있으면 거른다(앞/중간/끝/괄호), 비슷하지만 다른 글자나 빈 값은 거르지 않는다
assert kf is not None, "app/kakao_filters.py가 필요하다"
for title in ("단행본", "나 혼자 레벨업 단행본", "[단행본] 신의 탑", "제목(단행본판)", "제목 단행본 1권"): assert kf.is_excluded_title(title), title
for title in ("단행", "행본", "단 행 본", "정상 작품", "", None): assert not kf.is_excluded_title(title), repr(title)
print("1) 판정 OK")

# ── 카카오 카드 ──
def card(sid, title, authors="가", badge=None):
    return {"series_id": sid, "category_uid": kakao_api.WEBTOON_CATEGORY_UID, "title": title, "authors": authors, "age_grade": 0, "badge": badge, "on_issue": "Y"}
NORMAL, BOOK = card(1, "정상 작품", badge="UP"), card(2, "정상 작품 단행본", badge="UP")

async def main():
    # Scenario 2: 카드 → 항목 변환과 요일 목록 — 단행본 카드는 항목이 되지 않는다
    assert kakao_api._card_to_item(NORMAL) is not None and kakao_api._card_to_item(BOOK) is None
    async def fake_tab(session, tab_uid, timeout): return [NORMAL, BOOK], True
    real_tab = kakao_api._fetch_weekday_tab; kakao_api._fetch_weekday_tab = fake_tab
    items, ok_all = await kakao_api.fetch_weekday_catalog_checked(None, 5); assert [i["title_id"] for i in items] == [1] and ok_all, items
    assert [i["title_id"] for i in await kakao_api.fetch_weekday_catalog(None, 5, use_cache=False)] == [1]
    kakao_api._fetch_weekday_tab = real_tab
    print("2) 요일 목록 OK")

    # Scenario 3: 검색(수동 다운로드), 작가 검색(작가 신작 구독), 제목 검색(옛 기록의 번호 찾기) — 모두 단행본 제외. 못 찾은 제목의 후보에도 안 나온다
    async def fake_cards(session, keyword, timeout, pages): return [NORMAL, BOOK, card(3, "정상 작품 후보")]
    real_cards = kakao_api._search_webtoon_cards; kakao_api._search_webtoon_cards = fake_cards
    assert [i["title_id"] for i in await kakao_api.search_series(None, "정상", 5)] == [1, 3]
    assert [i["title_id"] for i in await kakao_api.search_by_author(None, "가", 5)] == [1, 3]
    found, candidates = await kakao_api.search_series_by_titles(None, ["정상 작품 단행본"], 5); assert found == [] and all("단행본" not in c["title_name"] for c in candidates.get("정상 작품 단행본", [])), (found, candidates)
    found, _ = await kakao_api.search_series_by_titles(None, ["정상 작품"], 5); assert [i["title_id"] for i in found] == [1]
    kakao_api._search_webtoon_cards = real_cards
    print("3) 검색/작가 검색 OK")

    # Scenario 4: 목록 캐시(디스크에 예전부터 남은 것) — 읽을 때 뺀다
    kakao_catalog._state["loaded"] = True
    kakao_catalog._state["items"] = [kakao_api._card_to_item(NORMAL), {**kakao_api._card_to_item(NORMAL), "title_id": 9, "title_name": "예전에 받아 둔 단행본"}]; kakao_catalog._state["fetched_at"] = 1_800_000_000.0
    assert [i["title_id"] for i in kakao_catalog.snapshot()[0]] == [1]
    print("4) 캐시 OK")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 5: 전체목록(카카오) — 카탈로그에 있든, DB 기록으로 보완되든 단행본은 안 나온다. 제외됨/구독해제 목록(/kakao-webtoons)에도 안 나온다
        repository.upsert_new_kakao_webtoon(1, "정상 작품", status=AC)
        repository.upsert_new_kakao_webtoon(11, "구독했던 단행본", status=UN); repository.set_kakao_ever_subscribed(11) if hasattr(repository, "set_kakao_ever_subscribed") else None
        repository.upsert_new_kakao_webtoon(12, "제외한 단행본", status=EX); repository.upsert_new_kakao_webtoon(13, "제외한 정상", status=EX)
        titles = [w["title"] for w in (await c.get("/api/kakao-list")).json()["items"]]
        assert "정상 작품" in titles and not any("단행본" in t for t in titles), titles
        excluded = [w["title"] for w in (await c.get("/api/kakao-webtoons", params={"status": "excluded"})).json()]; assert excluded == ["제외한 정상"], excluded
        unsub = [w["title"] for w in (await c.get("/api/kakao-webtoons", params={"status": "unsubscribed"})).json()]; assert not any("단행본" in t for t in unsub), unsub
        print("5) 전체목록/제외됨/구독해제 OK")

        # Scenario 6: 구독 요청(목록/수동 다운로드에서) — 단행본은 400으로 거절하고 DB에 남기지 않는다
        r = await c.post("/api/kakao-webtoons/804/subscribe", json={"title": "뭔가 단행본"}); assert r.status_code == 400 and "단행본" in r.json()["detail"], r.text
        assert not repository.kakao_webtoon_exists(804)
        print("6) 구독 거절 OK")

        # Scenario 7: 수동 다운로드 분석 — 작품 제목이 단행본이면 분석 결과를 보여 주지 않는다(404). 정상 작품은 그대로
        SITE["site"] = Site([item(1, free=True)], series_title="작품 단행본")
        r = await c.get("/api/kakao-manual/analyze", params={"series_id": 700}); assert r.status_code == 404 and "단행본" in r.json()["detail"], r.text
        SITE["site"] = Site([item(1, free=True)], series_title="정상 작품")
        assert (await c.get("/api/kakao-manual/analyze", params={"series_id": 701})).status_code == 200

    # Scenario 8: 받기 — 수동(download_selected)도 자동(run_download)도 단행본은 받지 않는다
    root = tempfile.mkdtemp()
    client = kp.KakaoPageClient(Site([item(1, free=True), item(2, free=True)], series_title="작품 단행본"), {}, 10)
    sel = await kp.download_selected(client, series_id=5, title=None, numbers=[1, 2], download_root=root, on_progress=None)
    assert sel.downloaded == [] and sel.not_found == [1, 2], sel
    auto = await kp.run_download(kp.KakaoPageClient(Site([item(1, free=True)], series_title="작품 단행본"), {}, 10), series_id=6, title=None, download_root=root, max_episodes=5, on_progress=None)
    assert auto.downloaded == [] and auto.error and "단행본" in auto.error, auto
    assert not any(Path(root).rglob("*.zip")), "파일이 생기면 안 된다"
    print("8) 받기 OK")
asyncio.run(main())

# Scenario 9: 새 에피소드 감시(구독해제 카카오) / 빠른 상태 스캔 — DB에 단행본 기록이 남아 있어도 확인하지 않는다
requested = []
class WatchClient:
    async def fetch_series_item(self, series_id): requested.append(series_id); return {"on_sale_count": 10}
repository.upsert_new_kakao_webtoon(21, "감시할 정상", status=UN); repository.upsert_new_kakao_webtoon(22, "감시 안 할 단행본", status=UN)
asyncio.run(new_episode_watch.scan_kakao(WatchClient(), get_settings())); assert 21 in requested and 22 not in requested, requested
repository.upsert_new_kakao_webtoon(23, "스캔할 정상", status=EX); repository.upsert_new_kakao_webtoon(24, "스캔 안 할 단행본", status=EX)
kakao_catalog._state["items"] = []
targets, _ = quick_scan.collect_targets(today="tue", max_age_hours=1)
ids = {t.title_id for t in targets if t.platform == "kakao"}; assert 23 in ids and 24 not in ids and 22 not in ids and 12 not in ids, ids
print("9) 감시/빠른 스캔 OK")
print("ALL OK")
