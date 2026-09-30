import os
import asyncio, io, json
from pathlib import Path
from unittest import mock
import httpx
from PIL import Image
from app import kakao_api, kakao_cover, archiver

SAMPLES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "kakao_page_samples.json"), encoding='utf-8'))
real_products = SAMPLES['content_product_list']
REAL_KID = real_products['result']['series_item']['thumbnail']

# 1) 실제 응답에서 공식 표지 kid를 꺼낸다 + 이미지 주소 조합(사용자가 확인해준 형태)
assert kakao_api.thumbnail_kid(real_products) == REAL_KID == 'cNduW3/hzwBmbpZKN/xr4RsAtlwzk7PIymGcBDtk'
assert kakao_api.thumbnail_kid({'result': {}}) is None and kakao_api.thumbnail_kid(None) is None
assert kakao_api.image_url('dIMleI/hynaFMwPhI/wmD6aoRrGdj8c5wo4BjKkk') == \
    'https://page-images.kakaoentcdn.com/download/resource?kid=dIMleI/hynaFMwPhI/wmD6aoRrGdj8c5wo4BjKkk&filename=o1'
assert kakao_api.image_url('K', kakao_api.IMAGE_FILENAME_CARD).endswith('&filename=o1/dims/resize/384')
assert 'Origin' not in kakao_api.IMAGE_HEADERS and kakao_api.IMAGE_HEADERS['Referer'] == 'https://page.kakao.com/'
print("1) kid 추출/이미지 주소/헤더 OK")

# 2) info.xml 주소 판별
p = kakao_cover.parse_kakao_web_url
assert p('https://page.kakao.com/content/61268805/') == '61268805'
assert p('https://page.kakao.com/content/61268805') == '61268805'
assert p('https://page.kakao.com/content/56939012/viewer/70776762/') == '56939012'
assert p('https://webtoon.kakao.com/content/관존-이강진/2155') is None   # 옛 카카오웹툰은 판별 불가 → 건드리지 않음
assert p('https://comic.naver.com/webtoon/list?titleId=812354') is None and p('') is None
assert p('https://page.kakao.com/menu/10010/screen/52') is None
print("2) info.xml 주소 판별 OK (카카오페이지만, 옛 카카오웹툰/네이버는 None)")

# 3) 이미지 변환: PNG(투명) → 흰 배경 JPEG, JPEG는 그대로
def img_bytes(fmt, mode='RGB', color=(10, 20, 30)):
    b = io.BytesIO(); Image.new(mode, (40, 60), color).save(b, fmt); return b.getvalue()
png_alpha = io.BytesIO(); Image.new('RGBA', (40, 60), (0, 0, 0, 0)).save(png_alpha, 'PNG')
out = kakao_cover._to_jpeg_bytes(png_alpha.getvalue())
im = Image.open(io.BytesIO(out)); assert im.format == 'JPEG' and im.getpixel((5, 5))[0] > 240   # 투명 → 흰색
jpg = img_bytes('JPEG'); assert kakao_cover._to_jpeg_bytes(jpg) == jpg                         # 재인코딩 안 함
assert Image.open(io.BytesIO(kakao_cover._to_jpeg_bytes(img_bytes('WEBP')))).format == 'JPEG'
assert kakao_cover._to_jpeg_bytes(b'not an image') is None
print("3) 이미지 변환 OK (투명→흰 배경 JPEG, JPEG 무변환, 깨진 파일 None)")

# 4) 공식 표지 받기 (requests 목킹) — 호출 순서/주소/헤더 확인
class R:
    def __init__(self, status, js=None, content=b''): self.status_code, self._js, self.content = status, js, content
    def json(self): return self._js
calls = []
def fake_get(url, params=None, headers=None, timeout=None):
    calls.append((url, params, headers))
    if 'product/list' in url: return R(200, real_products)
    return R(200, content=img_bytes('PNG'))
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    out = kakao_cover.fetch_official_cover_bytes('59248216')
assert out and Image.open(io.BytesIO(out)).format == 'JPEG'
assert calls[0][1]['series_id'] == 59248216 and calls[0][1]['sort_type'] == 'desc'
assert calls[1][0] == f'https://page-images.kakaoentcdn.com/download/resource?kid={REAL_KID}&filename=o1'
assert calls[1][2]['Referer'] == 'https://page.kakao.com/'
calls.clear()
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    kakao_cover.fetch_official_cover_bytes('59248216', filename=kakao_api.IMAGE_FILENAME_CARD)
assert calls[1][0].endswith('&filename=o1/dims/resize/384')
# 실패 케이스: 목록 403 / 표지 정보 없음 / 이미지 404 / 예외
with mock.patch.object(kakao_cover.requests, 'get', lambda *a, **k: R(403)): assert kakao_cover.fetch_official_cover_bytes('1') is None
with mock.patch.object(kakao_cover.requests, 'get', lambda *a, **k: R(200, {'result': {'series_item': {}}})): assert kakao_cover.fetch_official_cover_bytes('1') is None
def img404(url, **k): return R(200, real_products) if 'product/list' in url else R(404)
with mock.patch.object(kakao_cover.requests, 'get', img404): assert kakao_cover.fetch_official_cover_bytes('1') is None
def boom(*a, **k): raise RuntimeError('network')
with mock.patch.object(kakao_cover.requests, 'get', boom): assert kakao_cover.fetch_official_cover_bytes('1') is None
print("4) 공식 표지 받기 OK (원본/줄인 크기 주소, 헤더, 실패 4종 모두 None)")

# 5) 폴더 표지 교체: 카카오페이지 폴더만 교체, 옛 카카오웹툰/네이버/실패는 그대로
d = Path(os.environ['DATABASE_PATH']).parent / 'folder'
d.mkdir(parents=True, exist_ok=True)
def make(web):
    for f in d.glob('*'): f.unlink()
    (d / 'info.xml').write_text(f'<ComicInfo><Web>{web}</Web></ComicInfo>', encoding='utf-8')
    (d / 'cover.png').write_bytes(b'OLD-PNG')
make('https://page.kakao.com/content/61268805/')
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    assert kakao_cover.refresh_kakao_cover_if_applicable(d) is True
assert (d / 'cover.jpg').is_file() and not (d / 'cover.png').exists() and len(list(d.glob('cover.*'))) == 1
make('https://webtoon.kakao.com/content/x/2155')
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    assert kakao_cover.refresh_kakao_cover_if_applicable(d) is False
assert (d / 'cover.png').read_bytes() == b'OLD-PNG'                         # 옛 표지 그대로
make('https://comic.naver.com/webtoon/list?titleId=1')
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    assert kakao_cover.refresh_kakao_cover_if_applicable(d) is False
make('https://page.kakao.com/content/61268805/')
with mock.patch.object(kakao_cover.requests, 'get', lambda *a, **k: R(403)):
    assert kakao_cover.refresh_kakao_cover_if_applicable(d) is False
assert (d / 'cover.png').read_bytes() == b'OLD-PNG'                         # 실패해도 기존 표지 유지
print("5) 폴더 표지 교체 OK (카카오페이지만 교체, 그 외/실패는 원래 표지 유지)")

# 6) rclone 원본용 임시 파일
with mock.patch.object(kakao_cover.requests, 'get', fake_get):
    tmp = archiver._download_kakao_cover_to_temp_file('61268805')
assert tmp and Path(tmp).is_file() and Image.open(tmp).format == 'JPEG'; Path(tmp).unlink()
with mock.patch.object(kakao_cover.requests, 'get', lambda *a, **k: R(403)):
    assert archiver._download_kakao_cover_to_temp_file('61268805') is None
print("6) rclone용 임시 표지 파일 OK")

# 7) 목록 썸네일 엔드포인트
import app.main as m
async def main():
    fetched = []
    def fake_official(series_id, *, filename=kakao_api.IMAGE_FILENAME_ORIGINAL, timeout=15):
        fetched.append((series_id, filename))
        return None if series_id == '404404' else img_bytes('JPEG')
    kakao_cover.fetch_official_cover_bytes = fake_official
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='http://test') as c:
        r = await c.get('/api/kakao-thumbnail/61268805')
        assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg' and 'max-age' in r.headers['cache-control']
        assert fetched == [('61268805', 'o1/dims/resize/384')]           # 목록용은 줄인 크기
        r = await c.get('/api/kakao-thumbnail/61268805'); assert r.status_code == 200
        assert len(fetched) == 1                                          # 두 번째는 저장된 파일 → 카카오 안 부름
        assert (await c.get('/api/kakao-thumbnail/404404')).status_code == 404
        # 동시에 같은 작품 여러 번 요청해도 한 번만 받는다
        fetched.clear()
        rs = await asyncio.gather(*[c.get('/api/kakao-thumbnail/777') for _ in range(6)])
        assert all(x.status_code == 200 for x in rs) and len(fetched) == 1, fetched
    print("7) 썸네일 엔드포인트 OK (줄인 크기, 저장 후 재사용, 404, 동시 요청 1회만)")
asyncio.run(main())
print("\n전부 통과")
