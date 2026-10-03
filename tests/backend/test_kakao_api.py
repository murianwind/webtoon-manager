import os
import asyncio, json, copy
from app import kakao_api

SAMPLES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_page_samples.json"), encoding='utf-8'))
real_day = SAMPLES['landing_dayofweek']
real_search = SAMPLES['search_series']
real_products = SAMPLES['content_product_list']

class Resp:
    def __init__(self, status, data): self.status, self._d = status, data
    async def json(self): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class FakeSession:
    """url+params에 따라 응답을 만드는 가짜 세션. 호출 기록을 남긴다."""
    def __init__(self, handler): self.handler, self.calls = handler, []
    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        status, data = self.handler(url, params or {})
        return Resp(status, data)

def reset_cache(): kakao_api._catalog_cache.update(at=0.0, items=None)
kakao_api._REQUEST_INTERVAL_SECONDS = 0  # 테스트 속도

# 1) 실제 카드 → 항목 변환
cards = real_day['result']['list']
items = [kakao_api._card_to_item(c) for c in cards]
assert all(items) and len(items) == 25
by_title = {i['title_name']: i for i in items}
w = by_title['너에게 빼앗을 왕관']
assert w['has_update'] and not w['is_new'] and not w['is_paused']
assert w['author_names'] == ['스튜디오 이너스', '윌브라이트'], w['author_names']
assert w['thumbnail_url'].startswith('https://page-images.kakaoentcdn.com/download/resource?kid=s4YvT/dJMcahFWfai/ifCwWXCTGhOXs9rBG7WDW0'), w['thumbnail_url']
assert by_title['바이올렛 체로타의 졸속 결혼 [19세 완전판]']['is_adult'] is True
assert by_title['바이올렛 체로타의 졸속 결혼']['is_adult'] is False
assert kakao_api._split_author_names('효막, 넬피,HT 스튜디오,비츄,효막') == ['효막', '넬피', 'HT 스튜디오', '비츄']
# 웹소설(category 11)은 제외
novel = copy.deepcopy(cards[0]); novel['category_uid'] = 11
assert kakao_api._card_to_item(novel) is None
# 배지: BT03 = 신작, on_issue P = 휴재
c = copy.deepcopy(cards[0]); c['badge'] = 'BT03'; c['on_issue'] = 'P'
it = kakao_api._card_to_item(c); assert it['is_new'] and not it['has_update'] and it['is_paused']
# 카드 이미지 우선순위: card_set → card_img → banner
c = {'series_id': 1, 'category_uid': 10, 'asset_property': {'card_img': 'AAA/bbb/ccc'}}
assert 'kid=AAA/bbb/ccc' in kakao_api._card_to_item(c)['thumbnail_url']
assert kakao_api._card_to_item({'series_id': 1, 'category_uid': 10})['thumbnail_url'] == ''
print('1) 카드 변환 OK (배지/휴재/성인/저자/썸네일/웹소설 제외)')

# 2) 페이징 + 요일 중복 제거 + 카테고리 필터
def make_pages(tab_uid):
    p0 = copy.deepcopy(real_day); p0['result']['is_end'] = False
    p1 = copy.deepcopy(real_day); p1['result']['is_end'] = True
    for k, card in enumerate(p1['result']['list']):
        card['series_id'] = 900000000 + tab_uid * 1000 + k  # 요일별로 다른 작품
    p1['result']['list'].append(dict(novel, series_id=777))  # 웹소설이 섞여 나옴
    return [p0, p1]

def handler(url, params):
    tab = params['tab_uid']; page = params['page']
    pages = make_pages(tab)
    return 200, pages[page] if page < len(pages) else {'result': {'list': [], 'is_end': True}}

async def t2():
    reset_cache()
    s = FakeSession(handler)
    got = await kakao_api.fetch_weekday_catalog(s, 10)
    ids = [i['title_id'] for i in got]
    assert len(ids) == len(set(ids)), '중복 제거 실패'
    # p0의 25개는 7개 요일에서 똑같이 나오므로 1번만 → 25 + (요일별 p1 25개 x 7)
    assert len(ids) == 25 + 25 * 7, len(ids)
    assert 777 not in ids
    assert len(s.calls) == 7 * 2  # 요일당 2페이지
    # 첫 페이지엔 bm/subcategory_uid가 없고 이후엔 있다(사이트와 같은 형태)
    p_first = [c[1] for c in s.calls if c[1]['page'] == 0][0]; p_next = [c[1] for c in s.calls if c[1]['page'] == 1][0]
    assert 'bm' not in p_first and p_next['bm'] == 'A' and p_next['subcategory_uid'] == 0
    assert {c[1]['tab_uid'] for c in s.calls} == {1, 2, 3, 4, 5, 6, 7}
    assert all(c[1]['category_uid'] == 10 and c[1]['screen_uid'] == 52 for c in s.calls)
    print('2) 페이징/요일 중복 제거/웹소설 제외 OK, 호출', len(s.calls), '회')
    # 캐시: 두 번째는 호출 없이 반환
    n = len(s.calls)
    again = await kakao_api.fetch_weekday_catalog(s, 10)
    assert len(s.calls) == n and len(again) == len(got)
    # use_cache=False면 다시 호출
    await kakao_api.fetch_weekday_catalog(s, 10, use_cache=False)
    assert len(s.calls) == n * 2
    print('   캐시 적중/강제 새로고침 OK')
asyncio.run(t2())

# 3) 일부 요일 실패 → 있는 것만 반환하되 캐시하지 않음
async def t3():
    reset_cache()
    def h(url, params):
        if params['tab_uid'] == 3: return 500, None
        return handler(url, params)
    orig_sleep = asyncio.sleep
    async def fast_sleep(x): await orig_sleep(0)
    kakao_api.asyncio.sleep = fast_sleep  # 재시도 대기 생략
    try:
        s = FakeSession(h)
        got = await kakao_api.fetch_weekday_catalog(s, 10)
    finally:
        kakao_api.asyncio.sleep = orig_sleep
    assert len(got) > 0 and kakao_api._catalog_cache['items'] is None
    print('3) 일부 요일 실패 시 부분 결과 반환 + 캐시 안 함 OK')
asyncio.run(t3())

# 4) 최신 회차 URL
async def t4():
    s = FakeSession(lambda u, p: (200, {'result': {'list': [
        {'item': {'product_id': 111, 'title': '34화', 'hidden': True}},
        {'item': {'product_id': 222, 'title': '33화', 'is_free': False}},
        {'item': {'product_id': 333, 'title': '32화', 'is_free': True}}]}}))
    url = await kakao_api.fetch_latest_episode_url(s, 56566288, 10)
    assert url == 'https://page.kakao.com/content/56566288/viewer/222', url  # 숨김은 건너뛰고, 잠겨 있어도 최신
    u, p = s.calls[0]
    assert p['sort_type'] == 'desc' and p['series_id'] == 56566288 and p['cursor_direction'] == 'NEXT'
    # 실제 응답 모양(HAR)에서도 product_id를 잘 읽는지
    s2 = FakeSession(lambda u, p: (200, real_products))
    url2 = await kakao_api.fetch_latest_episode_url(s2, 59248216, 10)
    assert url2 and url2.startswith('https://page.kakao.com/content/59248216/viewer/'), url2
    # 빈 목록/실패
    assert await kakao_api.fetch_latest_episode_url(FakeSession(lambda u, p: (200, {'result': {'list': []}})), 1, 10) is None
    print('4) 최신 회차 URL OK:', url)
asyncio.run(t4())

# 5) 작가 검색 (실제 HAR 응답 — 웹툰/책 섞임, 저자 정확 일치만)
async def t5():
    s = FakeSession(lambda u, p: (200, real_search))
    got = await kakao_api.search_by_author(s, '연상호', 10)
    print('   검색 결과:', [(g['title_id'], g['title_name']) for g in got])
    assert got and all(set(g) == {'title_id', 'title_name', 'is_adult'} for g in got)
    titles = {g['title_name'] for g in got}
    assert '오늘의 SF [단행본]' not in titles  # 책(category 16)은 제외
    assert s.calls[0][1]['keyword'] == '연상호' and s.calls[0][1]['category_uid'] == 10
    none = await kakao_api.search_by_author(FakeSession(lambda u, p: (200, real_search)), '없는작가이름', 10)
    assert none == []
    print('5) 작가 검색 OK')
asyncio.run(t5())

# 6) 후보 작가
names = kakao_api.extract_candidate_author_names(items)
assert '윌브라이트' in names and names == sorted(set(names))
print('6) 후보 작가 OK', len(names), '명')
print('\n전부 통과')
