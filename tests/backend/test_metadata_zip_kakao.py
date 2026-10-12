"""수동 다운로드: 분석한 카카오페이지 웹툰의 표지(cover.jpg)와 info.xml만 zip 하나로 브라우저에 내려준다 — 로그인 쿠키 없이도 되고, 서버 폴더와 DB에는 아무것도 쓰지 않는다."""
import asyncio
import io
import os
import tempfile
import urllib.parse
import zipfile

import aiohttp
import httpx

from app import comicinfo, db, repository
from app import kakao_page_download as kp
from fake_kakao_rental_site import JPG, SITE, FakeSession, Site, item
db.get_connection()
import app.main as m

aiohttp.ClientSession = FakeSession
COVER = {"value": JPG}
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: COVER["value"]


async def main():
    root = tempfile.mkdtemp()
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, root)          # 쿠키는 저장하지 않는다(로그인 없이 되어야 한다)
    SITE["site"] = Site([item(1, free=True)])
    async with kp.new_session() as session:
        client = kp.client_or_anonymous(session, 10)
        series_item, about = await client.fetch_series_item(1), await client.fetch_about(1)
    expected_xml = comicinfo.build_kakao_comicinfo_xml(series_item, 1, about)
    def forbidden_write(*a, **k): raise AssertionError("받기만 하는 기능은 작가 정보 같은 DB 쓰기를 하지 않는다")
    repository.set_kakao_authors = forbidden_write                       # info.xml 쓰기(write_info_xml)와 달리 DB를 건드리면 안 된다

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 1: cover.jpg와 info.xml이 담긴 zip — 내용은 폴더에 저장하던 것과 같고, 이름은 작품 이름, 서버 폴더는 그대로
        r = await c.get("/api/kakao-manual/metadata-zip", params={"series_id": 1})
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip", (r.status_code, r.text[:200])
        assert "작품_표지정보.zip" in urllib.parse.unquote(r.headers["content-disposition"]) and "x-metadata-warning" not in r.headers
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert sorted(z.namelist()) == ["cover.jpg", "info.xml"], z.namelist()
            assert z.read("info.xml").decode("utf-8") == expected_xml and z.read("cover.jpg") == JPG
        assert os.listdir(root) == [], "서버 폴더에 아무것도 만들지 않는다"
        # Scenario 2: 표지를 못 받으면 info.xml만 담고 헤더로 알린다
        COVER["value"] = None
        r = await c.get("/api/kakao-manual/metadata-zip", params={"series_id": 1})
        assert r.status_code == 200 and r.headers["x-metadata-warning"] == "cover-missing"
        with zipfile.ZipFile(io.BytesIO(r.content)) as z: assert z.namelist() == ["info.xml"]
        # Scenario 3: 작품 정보를 못 받으면 404
        async def no_series(self, series_id): return None
        original = kp.KakaoPageClient.fetch_series_item
        kp.KakaoPageClient.fetch_series_item = no_series
        r = await c.get("/api/kakao-manual/metadata-zip", params={"series_id": 1})
        kp.KakaoPageClient.fetch_series_item = original
        assert r.status_code == 404 and "작품 정보" in r.json()["detail"], r.text
        assert (await c.get("/api/kakao-manual/metadata-zip")).status_code == 422
    print("1~3) 카카오 표지·정보 zip OK")

asyncio.run(main())
print("ALL OK")
