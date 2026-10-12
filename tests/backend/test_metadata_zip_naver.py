"""수동 다운로드: 분석한 네이버 웹툰의 표지(cover)와 info.xml만 zip 하나로 브라우저에 내려준다 — 서버 폴더에는 아무것도 쓰지 않는다.

info.xml 내용은 폴더에 저장하던 것(comicinfo.build_comicinfo_xml)과 같고, 표지는 원본 확장자 그대로 cover.<확장자>로 담긴다.
표지를 못 받으면 info.xml만 담고 헤더로 알린다(화면이 안내 창을 띄운다). 작품 정보를 못 받으면 404.
"""
import asyncio
import io
import os
import urllib.parse
import zipfile

import httpx

from app import comicinfo, db, download_roots, manual_download, metadata_zip, naver_api
from app.config import get_settings
from app.file_utils import remove_forbidden_str
from app.models import TitleInfo
db.get_connection()
import app.main as m

settings = get_settings()
INFO = TitleInfo(title_id="111", title_name="테스트 작품: 1부", synopsis="줄거리", is_adult=False, webtoon_type="webtoon", is_finished=False, thumbnail_url="https://img/111.png")
COVER = {"value": (".png", b"\x89PNGdata")}
INFO_OK = {"value": INFO}


async def fake_info(session, title_id, timeout): return INFO_OK["value"]
async def fake_cover(session, info, timeout): return COVER["value"]
naver_api.fetch_title_info = fake_info
manual_download.fetch_cover_image = fake_cover


def helpers():
    # Scenario 1: zip 만들기와 한글 파일 이름 헤더(RFC 5987) — 브라우저가 한글 이름 그대로 저장한다
    data = metadata_zip.build_zip({"info.xml": b"<x/>", "cover.png": b"PNG"})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["cover.png", "info.xml"] and z.read("info.xml") == b"<x/>" and z.read("cover.png") == b"PNG"
    headers = metadata_zip.attachment_headers("테스트 작품_표지정보.zip", ["cover-missing"])
    assert "filename*=UTF-8''" + urllib.parse.quote("테스트 작품_표지정보.zip") in headers["Content-Disposition"] and headers["Content-Disposition"].startswith("attachment;")
    assert headers["X-Metadata-Warning"] == "cover-missing" and "X-Metadata-Warning" not in metadata_zip.attachment_headers("a.zip")
    print("1) zip/헤더 도우미 OK")


async def endpoint():
    root = download_roots.naver_root(settings)
    before = sorted(os.listdir(root)) if os.path.isdir(root) else None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 2: 표지와 info.xml이 담긴 zip, 파일 이름에 작품 이름, 서버 폴더는 그대로
        r = await c.get("/api/manual-download/metadata-zip", params={"title_id": "111"})
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip", (r.status_code, r.text[:200])
        disposition = urllib.parse.unquote(r.headers["content-disposition"])
        assert f"{remove_forbidden_str(INFO.title_name)}_표지정보.zip" in disposition and "테스트 작품" in disposition, disposition   # 작품 폴더 이름과 같은 규칙
        assert "x-metadata-warning" not in r.headers
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert sorted(z.namelist()) == ["cover.png", "info.xml"], z.namelist()
            assert z.read("info.xml").decode("utf-8") == comicinfo.build_comicinfo_xml(INFO) and z.read("cover.png") == b"\x89PNGdata"
        assert (sorted(os.listdir(root)) if os.path.isdir(root) else None) == before, "서버 폴더에 아무것도 만들지 않는다"
        # Scenario 3: 표지를 못 받으면 info.xml만 담고 헤더로 알린다
        COVER["value"] = None
        r = await c.get("/api/manual-download/metadata-zip", params={"title_id": "111"})
        assert r.status_code == 200 and r.headers["x-metadata-warning"] == "cover-missing"
        with zipfile.ZipFile(io.BytesIO(r.content)) as z: assert z.namelist() == ["info.xml"]
        # Scenario 4: 작품 정보를 못 받으면 404와 이유
        INFO_OK["value"] = None
        r = await c.get("/api/manual-download/metadata-zip", params={"title_id": "999"})
        assert r.status_code == 404 and "작품 정보" in r.json()["detail"], r.text
        # Scenario 5: titleId가 없으면 요청 오류
        assert (await c.get("/api/manual-download/metadata-zip")).status_code == 422
    print("2~5) 네이버 표지·정보 zip OK")

helpers()
asyncio.run(endpoint())
print("ALL OK")
