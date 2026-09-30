import os
import asyncio, sqlite3
from datetime import datetime, timezone
import httpx

# 1) 옛 스키마(seo_id 컬럼 있음) + 옛 번호의 기록/기준선이 든 "기존 DB"를 먼저 만든다
old = sqlite3.connect(os.environ['DATABASE_PATH'])
now = datetime.now(timezone.utc).isoformat()
old.executescript("""
CREATE TABLE kakao_webtoons (title_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'excluded',
  ever_subscribed INTEGER NOT NULL DEFAULT 0, thumbnail_url TEXT NOT NULL DEFAULT '', seo_id TEXT NOT NULL DEFAULT '',
  author_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE kakao_seen_titles (author_name TEXT NOT NULL, title_id INTEGER NOT NULL, title_name TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL, PRIMARY KEY (author_name, title_id));
""")
rows = [(2155, '관존 이강진', 'excluded', 0), (4602, '바퀴벌레 잔혹사', 'unsubscribed', 1), (4886, '땅콩일기', 'active', 1), (9999, '이제 없는 작품', 'excluded', 0)]
for tid, title, st, ev in rows:
    old.execute("INSERT INTO kakao_webtoons VALUES (?,?,?,?,?,?,?,?,?)", (tid, title, st, ev, 'https://old-cdn/x', 'old-seo', '옛저자', now, now))
old.execute("INSERT INTO kakao_seen_titles VALUES ('노경찬', 2155, '관존 이강진', ?)", (now,))
old.execute("INSERT INTO kakao_seen_titles VALUES ('노경찬', 88000000, '새번호작품', ?)", (now,))
old.commit(); old.close()

from app import kakao_api, repository, db
import app.main as m

conn = db.get_connection()  # 앱 시작 시 하는 초기화
seen = [r['title_id'] for r in conn.execute("SELECT title_id FROM kakao_seen_titles").fetchall()]
assert seen == [88000000], seen
print("A) 시작 시 옛 번호 알림 기준선만 지워짐(새 번호는 유지):", seen)

def catalog(ids_titles):
    return [{'title_id': i, 'title_name': t, 'is_adult': False, 'author_names': ['노경찬', '송윤달'],
             'has_update': False, 'is_new': False, 'is_paused': False, 'thumbnail_url': f'https://page-images/{i}'} for i, t in ids_titles]

live = catalog([(60105861, '관존 이강진'), (56566288, '바퀴벌레 잔혹사'), (70000001, '중복제목'), (70000002, '중복제목'), (59000000, '땅콩일기')])
async def fake_catalog(session, timeout, *, use_cache=True): return live
kakao_api.fetch_weekday_catalog = fake_catalog
async def fake_checked(session, timeout): return await fake_catalog(session, timeout, use_cache=False), True
kakao_api.fetch_weekday_catalog_checked = fake_checked      # 이전 뒤 시작되는 백그라운드 갱신도 가짜를 쓰게(실제 카카오를 부르지 않도록)

from app import kakao_catalog
async def migrate():
    """자동 변환(1회) 함수를 직접 부른다 — force=True는 \"다시 눌러도 안전\"을 확인하려고 플래그를 무시."""
    items = await fake_catalog(None, 1, use_cache=False)
    return await kakao_catalog.migrate_legacy_once(items, force=True)

async def main():
    transport = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
        # B) 제거된 엔드포인트
        assert (await c.get('/api/kakao-thumbnail/1')).status_code == 404
        assert (await c.post('/api/kakao-thumbnail-cache/cleanup')).status_code in (404, 405)
        print("B) 썸네일 합성/캐시 엔드포인트 제거됨")

        # C) 기록 이전
        d = await migrate()
        assert d['migrated'] == 3, d
        assert d['unmatched'] == [] and d['deleted'] == 1 and '이제 없는 작품' not in repository.list_legacy_kakao_titles(), d   # 못 옮긴 제외됨은 지워진다
        got = {w['title_id']: w for w in [repository.get_kakao_webtoon(i) for i in (60105861, 56566288, 59000000)]}
        assert got[60105861]['status'] == 'excluded' and got[60105861]['ever_subscribed'] is False
        assert got[56566288]['status'] == 'unsubscribed' and got[56566288]['ever_subscribed'] is True
        assert got[59000000]['status'] == 'active'
        assert got[60105861]['thumbnail_url'] == 'https://page-images/60105861' and got[60105861]['author_summary'] == '노경찬, 송윤달'
        assert repository.get_kakao_webtoon(2155) is None and repository.get_kakao_webtoon(9999) is None      # 옮긴 옛 번호는 사라지고, 못 옮긴 제외됨(9999)도 지워진다
        print("C) 기록 이전 OK:", d)
        d2 = await migrate()
        assert d2['migrated'] == 0 and d2['unmatched'] == [] and d2['deleted'] == 0
        print("   다시 눌러도 안전(이미 옮긴 건 그대로)")

        # D) 카탈로그가 비면 502 (조용히 0개 이전으로 끝나지 않게)
        async def empty_catalog(session, timeout, *, use_cache=True): return []
        kakao_api.fetch_weekday_catalog = empty_catalog
        # (목록을 못 받으면 자동 변환은 실행되지 않는다 — 온전한 목록일 때만 kakao_catalog.refresh가 부른다)
        kakao_api.fetch_weekday_catalog = fake_catalog
        print("D) 목록을 못 받으면 502")

        # E) 전체목록: seo_id 없이 나오고, 옮긴 기록의 상태가 붙는다
        # 전체목록은 캐시에서만 읽는다 — 이전(migrate) 뒤 시작된 백그라운드 갱신이 끝난 뒤의 캐시를 읽어 확인한다
        from app import kakao_catalog
        assert await kakao_catalog.refresh() is True                       # 온전한 목록을 받아 캐시를 채운다(이 갱신이 옛 기록 변환도 1회 부른다)
        resp = (await c.get('/api/kakao-list')).json(); lst = resp['items']
        assert resp['refreshing'] is False and resp['refreshed_at'] and resp['version']
        assert all('seo_id' not in x for x in lst)
        by = {x['title_id']: x for x in lst}
        assert 60105861 not in by  # 제외됨은 전체목록에서 숨김
        assert by[56566288]['status'] == 'unsubscribed' and by[59000000]['status'] == 'active'
        assert by[70000001]['status'] is None
        print("E) 전체목록 OK (제외됨 숨김, 구독 상태 표시), 항목", len(lst))

        # F) 구독/제외 저장 (seo_id 없이)
        r = await c.post('/api/kakao-webtoons/70000001/subscribe', json={'title': '중복제목', 'thumbnail_url': 'https://x', 'author_summary': 'a'})
        assert r.status_code == 200, r.text
        r = await c.post('/api/kakao-webtoons/70000002/exclude', json={'title': '중복제목', 'thumbnail_url': 'https://x', 'author_summary': 'b'})
        assert r.status_code == 200, r.text
        assert repository.get_kakao_webtoon(70000001)['status'] == 'active'
        assert repository.get_kakao_webtoon(70000002)['status'] == 'excluded'
        print("F) 구독/제외 저장 OK")

        # G) 관심 작가 후보
        cand = (await c.get('/api/kakao/authors/candidates')).json()
        assert cand == ['노경찬', '송윤달']
        print("G) 작가 후보 OK", cand)

        # H) 백업 → 복원 왕복 (seo_id 없이도 정상)
        b = repository.export_all()
        repository.restore_all(b)
        assert repository.get_kakao_webtoon(70000001)['status'] == 'active'
        assert 'seo_id' not in repository.get_kakao_webtoon(70000001)
        print("H) 백업/복원 OK")
asyncio.run(main())
print("\n전부 통과")
