"""옛 카카오웹툰 기록 이전 — 제목 비교를 느슨하게(공백/문장부호 무시) + 못 옮기면 이유와 후보를 알려준다."""
import asyncio
import os
import sqlite3
from datetime import datetime, timezone
import httpx

DB = os.environ["DATABASE_PATH"]
old = sqlite3.connect(DB); now = datetime.now(timezone.utc).isoformat()
old.executescript("""CREATE TABLE kakao_webtoons (title_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'excluded',
  ever_subscribed INTEGER NOT NULL DEFAULT 0, thumbnail_url TEXT NOT NULL DEFAULT '', author_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);""")
legacy = [
    (101, "이 헌터 실화냐 ", "excluded"),                 # 제목 끝 공백(사용자 목록의 "이 헌터 실화냐 (제외됨)")
    (102, "영웅, 회귀하다 ", "excluded"),                 # 공백 + 문장부호 차이
    (103, "일코하는 황녀님 [19세 완전판]", "excluded"),   # 대괄호 포함 제목
    (104, "붉은 줄", "excluded"),                          # 같은 제목이 둘 → 모호
    (105, "타원을 그리는 법", "excluded"),                # 검색해도 다른 제목만 나옴 → 후보 표시
    (106, "룬의 아이들", "unsubscribed"),                 # 아예 검색 결과 없음
    (107, "관존 이강진", "excluded"),                     # 요일 목록에 있음(검색 불필요)
]
for tid, t, st in legacy: old.execute("INSERT INTO kakao_webtoons VALUES (?,?,?,?,?,?,?,?)", (tid, t, st, 1 if st == "unsubscribed" else 0, "old", "", now, now))
old.commit(); old.close()

from app import db, repository, kakao_api
from app.file_utils import title_key
import app.main as m
db.get_connection()

# ── 1. 제목 키 ──
assert title_key("이 헌터 실화냐 ") == title_key("이 헌터  실화냐") == title_key("이헌터실화냐")
assert title_key("영웅, 회귀하다") == title_key("영웅 회귀하다") == title_key("영웅! 회귀하다…")
assert title_key("일코하는 황녀님 [19세 완전판]") != title_key("일코하는 황녀님")          # 완전판은 다른 작품
assert title_key("Solo Leveling") == title_key("solo leveling") and title_key("...") == ""
print("1) 제목 키 OK (공백/문장부호/대소문자 무시, 판본 표기는 구분)")

# ── 2. 검색 함수: 느슨한 일치 + 후보 ──
kakao_api._REQUEST_INTERVAL_SECONDS = 0
B = 71000000  # 실제 카카오페이지 series_id처럼 8자리 번호(옛 번호 기준 100만 미만과 구분되도록)
def card(i, t, **k): return {"series_id": B + i, "category_uid": 10, "title": t, "authors": "작가", "age_grade": 0, "on_issue": k.get("on_issue", "Y"), "thumbnail": "K/a/b"}
RESULTS = {
    "이 헌터 실화냐": [card(1, "이 헌터, 실화냐?!")],
    "영웅, 회귀하다": [card(2, "영웅 회귀하다")],
    "일코하는 황녀님 [19세 완전판]": [card(3, "일코하는 황녀님"), card(4, "일코하는 황녀님 [19세 완전판]")],
    "붉은 줄": [card(5, "붉은 줄"), card(6, "붉은 줄")],
    "타원을 그리는 법": [card(7, "타원을 그리는 방법"), card(8, "타원의 비밀"), card(9, "전혀 다른 작품"), card(10, "네번째")],
    "룬의 아이들": [],
}
class Resp:
    def __init__(self, d): self.status, self._d = 200, d
    async def json(self, content_type=None): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Sess:
    def __init__(self): self.calls = []
    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        self.calls.append(dict(params)); return Resp({"result": {"list": RESULTS.get(params["keyword"], []), "is_end": True}})
async def t2():
    s = Sess()
    found, cands = await kakao_api.search_series_by_titles(s, ["이 헌터 실화냐 ", "영웅, 회귀하다 ", "일코하는 황녀님 [19세 완전판]", "붉은 줄", "타원을 그리는 법", "룬의 아이들"], 10)
    assert [c["keyword"] for c in s.calls] == ["이 헌터 실화냐", "영웅, 회귀하다", "일코하는 황녀님 [19세 완전판]", "붉은 줄", "타원을 그리는 법", "룬의 아이들"]   # 검색어는 공백을 정리해서
    ids = sorted(x["title_id"] for x in found)
    assert ids == [B + 1, B + 2, B + 4, B + 5, B + 6], ids                                # 완전판은 4번만, 붉은 줄은 둘 다(모호), 타원/룬은 없음
    assert set(cands) == {"타원을 그리는 법", "룬의 아이들"} and [c["title_id"] for c in cands["타원을 그리는 법"]] == [B + 7, B + 8, B + 9] and cands["룬의 아이들"] == []
    assert found[0]["thumbnail_url"].startswith("https://page-images") and found[0]["is_finished"] is False
    print("2) 제목 검색 OK (느슨한 일치, 완전판 구분, 후보 3개까지)")
asyncio.run(t2())

# ── 3. 이전 API ──
kakao_api.fetch_weekday_catalog = None
async def catalog(session, timeout, *, use_cache=True):
    return [{"title_id": 60105861, "title_name": "관존 이강진", "is_adult": False, "author_names": ["노경찬"], "has_update": False, "is_new": False, "is_paused": False, "thumbnail_url": "https://page-images/60105861", "is_finished": False}]
kakao_api.fetch_weekday_catalog = catalog
real_search = kakao_api.search_series_by_titles
searched = []
async def spy(session, titles, timeout):
    searched.append(list(titles)); return await real_search(session, titles, timeout)
kakao_api.search_series_by_titles = spy
import aiohttp
aiohttp.ClientSession = lambda *a, **k: Sess()  # 검색은 가짜 세션으로
class Wrap(Sess):
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
aiohttp.ClientSession = lambda *a, **k: Wrap()

from app import kakao_catalog
async def migrate():
    items = await catalog(None, 1, use_cache=False)
    return await kakao_catalog.migrate_legacy_once(items, force=True)

async def t3():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test"):
        d = await migrate()
        assert "관존 이강진" not in searched[0] and len(searched[0]) == 6                       # 요일 목록에 있는 건 검색 안 함
        assert d["migrated"] == 4, d                                                              # 관존(요일 목록) + 이헌터 + 영웅 + 완전판(검색)
        mig = {t: repository.get_kakao_webtoon(i) for i, t in ((60105861, "관존"), (B + 1, "이헌터"), (B + 2, "영웅"), (B + 4, "완전판"))}
        assert all(mig.values())
        assert repository.get_kakao_webtoon(B + 1)["status"] == "excluded" and repository.get_kakao_webtoon(B + 4)["title"] == "일코하는 황녀님 [19세 완전판]"
        # 못 옮긴 "제외됨"(붉은 줄=동명, 타원을 그리는 법=못 찾음)은 지워지고, 구독해제 이력이 있던 "룬의 아이들"만 이유와 함께 남는다
        assert d["deleted"] == 2 and [u["title"] for u in d["unmatched"]] == ["룬의 아이들"], d
        u = d["unmatched"][0]; assert "못 찾" in u["reason"] and u["candidates"] == [] and u["status"] == "unsubscribed"
        assert repository.get_kakao_webtoon(104) is None and repository.get_kakao_webtoon(105) is None and repository.get_kakao_webtoon(106) is not None
        print("3) 이전 OK:", d["migrated"], "개 이전 /", d["deleted"], "개(못 옮긴 제외됨) 삭제 / 이력 있는 1개는 이유 표시")
        d2 = await migrate()
        assert d2["migrated"] == 0 and d2["deleted"] == 0 and searched[1] == ["룬의 아이들"]
        print("   다시 눌러도 안전, 남은 것만 재시도")
asyncio.run(t3())
print("\n전부 통과")
