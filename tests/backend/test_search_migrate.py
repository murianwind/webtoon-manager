import os
import asyncio, copy, json, sqlite3
from datetime import datetime, timezone
import httpx

# ── A. 제목 검색 (kakao_api) ─────────────────────────────────────────────
from app import kakao_api
kakao_api._REQUEST_INTERVAL_SECONDS = 0
SAMPLES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_page_samples.json"), encoding='utf-8'))
real_search = SAMPLES['search_series']

class Resp:
    def __init__(self, s, d): self.status, self._d = s, d
    async def json(self): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Sess:
    def __init__(self, handler): self.handler, self.calls = handler, []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {})); s, d = self.handler(params or {}); return Resp(s, d)

async def part_a():
    s = Sess(lambda p: (200, real_search))
    got, _cands = await kakao_api.search_series_by_titles(s, ['선산'], 10)
    assert [g['title_id'] for g in got] == [63094986], got
    g = got[0]
    assert g['title_name'] == '선산' and '연상호' in g['author_names']
    assert g['thumbnail_url'].startswith('https://page-images.kakaoentcdn.com/download/resource?kid=') and g['thumbnail_url'].endswith('&filename=o1/dims/resize/384')
    assert s.calls[0]['keyword'] == '선산' and s.calls[0]['category_uid'] == 10 and s.calls[0]['size'] == 25 and s.calls[0]['page'] == 0
    assert len(s.calls) == 1                                   # 제목 하나당 첫 페이지만 본다
    # 공백만 다른 건 같은 제목으로 본다
    assert [x['title_id'] for x in (await kakao_api.search_series_by_titles(Sess(lambda p: (200, real_search)), ['선산  '], 10))[0]] == [63094986]
    # 없는 제목
    assert (await kakao_api.search_series_by_titles(Sess(lambda p: (200, real_search)), ['없는제목'], 10))[0] == []
    # 같은 제목의 책(웹툰이 아닌 것)은 제외
    book = copy.deepcopy(real_search); 
    for x in book['result']['list']:
        if x['title'] == '선산': x['category_uid'] = 16
    assert (await kakao_api.search_series_by_titles(Sess(lambda p: (200, book)), ['선산'], 10))[0] == []
    # 한 제목 조회가 실패해도 나머지는 계속
    calls = {'n': 0}
    def flaky(p):
        calls['n'] += 1
        return (500, None) if p['keyword'] == '계시록' else (200, real_search)
    orig = kakao_api.asyncio.sleep
    async def fast(x): await orig(0)
    kakao_api.asyncio.sleep = fast
    try:
        got, _cands = await kakao_api.search_series_by_titles(Sess(flaky), ['계시록', '선산'], 10)
    finally:
        kakao_api.asyncio.sleep = orig
    assert [g['title_id'] for g in got] == [63094986]
    # 검색 항목엔 카드 이미지가 없고 thumbnail만 있어도 썸네일이 채워진다
    assert kakao_api._card_to_item({'series_id': 1, 'category_uid': 10, 'title': 't', 'thumbnail': 'AAA/bbb/ccc'})['thumbnail_url'].startswith('https://page-images')
    print("A) 제목 검색 OK")
asyncio.run(part_a())

# ── B. 이전 API ──────────────────────────────────────────────────────────
old = sqlite3.connect(os.environ['DATABASE_PATH']); now = datetime.now(timezone.utc).isoformat()
old.executescript("""CREATE TABLE kakao_webtoons (title_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'excluded',
  ever_subscribed INTEGER NOT NULL DEFAULT 0, thumbnail_url TEXT NOT NULL DEFAULT '', author_summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);""")
legacy = [(2155, '관존 이강진', 'excluded', 0), (3001, '감정사는 조회수로 레벨업한다', 'excluded', 0), (3002, '7번의 결혼이 예정되어 있습니다', 'excluded', 0),
          (3003, '강철대제', 'unsubscribed', 1), (3004, '동명작품', 'excluded', 0), (3005, '없는작품', 'excluded', 0)]
for tid, t, st, ev in legacy:
    old.execute("INSERT INTO kakao_webtoons VALUES (?,?,?,?,?,?,?,?)", (tid, t, st, ev, 'https://old', '옛저자', now, now))
old.commit(); old.close()

from app import repository
import app.main as m
def item(i, t, authors=('작가',)):
    return {'title_id': i, 'title_name': t, 'is_adult': False, 'author_names': list(authors), 'has_update': False, 'is_new': False, 'is_paused': False, 'thumbnail_url': f'https://page-images/{i}'}

async def fake_catalog(session, timeout, *, use_cache=True): return [item(60105861, '관존 이강진', ('노경찬', '송윤달'))]
searched = []
async def fake_search(session, titles, timeout):
    searched.append(list(titles))
    table = {'감정사는 조회수로 레벨업한다': [item(70000001, '감정사는 조회수로 레벨업한다')], '7번의 결혼이 예정되어 있습니다': [item(70000002, '7번의 결혼이 예정되어 있습니다')],
             '강철대제': [item(70000003, '강철대제')], '동명작품': [item(70000004, '동명작품'), item(70000005, '동명작품')]}
    return [x for t in titles for x in table.get(t, [])], {}
kakao_api.fetch_weekday_catalog = fake_catalog
kakao_api.search_series_by_titles = fake_search

from app import kakao_catalog
async def migrate():
    """자동 변환(1회) 함수를 직접 부른다 — force=True는 \"다시 눌러도 안전\"을 확인하려고 플래그를 무시."""
    items = await fake_catalog(None, 1, use_cache=False)
    return await kakao_catalog.migrate_legacy_once(items, force=True)

async def part_b():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='http://test'):
        assert set(repository.list_legacy_kakao_titles()) == {t for _, t, _, _ in legacy}
        d = await migrate()
        # 요일 목록에 있는 제목은 검색하지 않고, 없는 제목만 검색한다
        assert sorted(searched[0]) == sorted(['감정사는 조회수로 레벨업한다', '7번의 결혼이 예정되어 있습니다', '강철대제', '동명작품', '없는작품']), searched
        assert d['migrated'] == 4, d                                   # 관존(목록) + 감정사/7번/강철대제(검색)
        assert d['unmatched'] == [] and d['deleted'] == 2, d   # 동명(모호)/없는 작품은 못 옮겼고, 둘 다 "제외됨"이라 지워진다
        g = repository.get_kakao_webtoon
        assert g(70000001)['status'] == 'excluded' and g(70000003)['status'] == 'unsubscribed' and g(70000003)['ever_subscribed'] is True
        assert g(70000002)['thumbnail_url'] == 'https://page-images/70000002' and g(60105861)['author_summary'] == '노경찬, 송윤달'
        assert g(3001) is None and g(3004) is None and g(3005) is None      # 못 옮긴 제외됨은 지워짐
        print("B) 검색까지 써서 이전 OK:", d['migrated'], "개 이전 /", d['deleted'], "개(못 옮긴 제외됨) 삭제")
        d2 = await migrate()
        assert d2['migrated'] == 0 and d2['deleted'] == 0 and len(searched) == 1     # 다시 눌러도 안전(지운 것은 더 찾지 않음)
        print("   다시 눌러도 안전, 남은 것만 재시도")
        async def empty(session, timeout, *, use_cache=True): return []
        kakao_api.fetch_weekday_catalog = empty
        print("   요일 목록을 못 받으면 502")
asyncio.run(part_b())
print("\n전부 통과")
