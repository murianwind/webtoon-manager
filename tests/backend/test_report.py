import os
import asyncio, copy, json
from app import db, repository, scheduler, kakao_api
db.get_connection()
kakao_api._REQUEST_INTERVAL_SECONDS = 0
kakao_api._catalog_cache.update(at=0.0, items=None)

SAMPLES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_page_samples.json"), encoding='utf-8'))
real_day = SAMPLES['landing_dayofweek']
cards = real_day['result']['list']
up = [c for c in cards if c.get('badge') == 'BT02']
assert len(up) >= 5, len(up)

# 상태 준비: 구독중/제외/구독해제/목록(구독 기록은 있지만 구독은 안 함)/기록 없음
sid = lambda i: up[i]['series_id']
repository.upsert_new_kakao_webtoon(sid(0), up[0]['title'], status=repository.STATUS_ACTIVE)
repository.upsert_new_kakao_webtoon(sid(1), up[1]['title'], status=repository.STATUS_EXCLUDED)
repository.upsert_new_kakao_webtoon(sid(2), up[2]['title'], status=repository.STATUS_ACTIVE)
repository.set_kakao_webtoon_status(sid(2), repository.STATUS_UNSUBSCRIBED)
repository.upsert_new_kakao_webtoon(sid(4), up[4]['title'], status=repository.STATUS_ACTIVE)
repository.set_kakao_webtoon_status(sid(4), repository.STATUS_UNREGISTERED)     # "목록으로"로 되돌려 구독은 안 하지만 전체목록에서 지켜보는 작품

class Resp:
    def __init__(self, s, d): self.status, self._d = s, d
    async def json(self): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Sess:
    def __init__(self): self.calls = []
    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if 'dayofweek' in url:
            page = copy.deepcopy(real_day); page['result']['is_end'] = True   # 한 페이지로 끝
            return Resp(200, page)
        if 'product/list' in url:
            sid_ = params['series_id']
            return Resp(200, {'result': {'list': [{'item': {'product_id': sid_ * 10 + 1, 'hidden': False}}]}})
        raise AssertionError(url)

class Settings:
    request_timeout_seconds = 10; artist_scan_concurrency = 5; delay_seconds = 0
    database_path = os.environ['DATABASE_PATH']

async def main():
    s = Sess()
    looked_up = []
    async def fake_reader(session, base, name, timeout): looked_up.append(name); return 'http://viewer/x'
    scheduler.webtoon_server_client.fetch_reader_url = fake_reader                  # 뷰어 서버가 설정돼 있어도 쓰이지 않아야 한다
    repository.set_setting('webtoon_server_url', 'http://viewer')
    results = await scheduler._collect_kakao_new_episodes(s, Settings())
    got = {r[0]: r for r in results}
    ids_up = {c['series_id'] for c in cards if c.get('badge') == 'BT02'}
    # "전체목록의 미구독 웹툰"만: 구독 중/구독해제/제외됨은 빠지고, 목록 상태와 기록 없는 작품은 남는다(네이버와 같은 취지)
    assert set(got) == ids_up - {sid(0), sid(1), sid(2)}, (set(got), ids_up)
    assert sid(0) not in got and sid(4) in got and sid(3) in got
    assert all(len(r) == 3 for r in results)                                       # (title_id, title_name, 바로가기 URL) — 구독 여부는 더 이상 필요 없다
    assert looked_up == [], looked_up                                              # 구독하지 않은 작품은 뷰어에 없으니 뷰어 조회를 하지 않고 카카오페이지 링크를 쓴다
    assert got[sid(3)][2] == f'https://page.kakao.com/content/{sid(3)}/viewer/{sid(3)*10+1}'
    # 리포트는 화면 캐시가 아니라 새 조회(요일 7탭이 실제로 호출됨)
    assert sum('dayofweek' in c[0] for c in s.calls) == 7
    print('리포트 수집 OK: 후보', len(got), '개 / 요일 호출', 7, '회 / 회차 호출', sum('product/list' in c[0] for c in s.calls), '회')
    msg = scheduler._build_report_message([], [], {}, [], 'https://app.example.com', kakao_new_episodes=results)
    assert '[카카오]' in msg and 'page.kakao.com/content/' in msg
    assert msg.count('[목록 제외]') == len(results)                                 # 미구독 작품이니 전부 "목록 제외" 링크가 붙는다
    print('리포트 메시지 OK')
asyncio.run(main())
