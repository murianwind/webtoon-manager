"""
카카오페이지 표지(cover.jpg) 받기 — 완결 처리로 옮겨지는 폴더의 표지를, 카카오페이지 작품 페이지에
나오는 공식 표지(제목이 들어간 완성 이미지)로 교체한다. 예전 카카오웹툰 시절엔 배경/캐릭터/제목
로고 소재를 따로 받아 직접 합성해야 했지만(webtoon_metadata.py에서 이식했던 로직), 카카오페이지는
완성된 표지를 주소 하나로 그대로 내려받을 수 있어서 합성하지 않고 그냥 받는다.

이 폴더가 카카오페이지 작품인지는 info.xml의 <Web> 태그(page.kakao.com/content/{series_id})로
판단한다. 옛 카카오웹툰 주소(webtoon.kakao.com)로 적힌 info.xml은 작품 번호 체계가 달라 카카오
페이지의 어느 작품인지 알 수 없으므로 건드리지 않는다(원래 있던 표지를 그대로 둔다). info.xml이
없는 폴더도 카카오인지 알 방법이 없어서 건너뛴다.

완결 아카이빙(keep_last=False) 시에만 archiver.py에서 호출된다 — 주기/수동 부분이동에서는 안 쓴다.
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

from app import kakao_api

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

log = logging.getLogger(__name__)

_KAKAO_PAGE_HOST = "page.kakao.com"


def parse_kakao_web_url(url: str) -> str | None:
    """info.xml의 <Web> URL이 카카오페이지 작품이면 series_id(문자열)를, 아니면(네이버, 옛 카카오웹툰
    등) None을 반환한다. `/content/{series_id}` 뒤에 `/viewer/{회차}` 등이 붙어 있어도 된다."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc.lower() != _KAKAO_PAGE_HOST:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) >= 2 and segments[0] == "content" and segments[1].isdigit():
        return segments[1]
    return None


def find_kakao_series_id_for_folder(webtoon_dir: Path) -> str | None:
    """이 폴더의 info.xml <Web> 태그를 읽어서 카카오페이지면 series_id를, 아니면 None을 반환한다.
    info.xml이 없거나 읽을 수 없으면 조용히 None (카카오 여부를 알 방법이 없으니 건드리지 않는 게
    안전)."""
    info_path = webtoon_dir / "info.xml"
    if not info_path.is_file():
        return None
    try:
        tree = ET.parse(info_path)
        web_elem = tree.getroot().find("Web")
        web_url = (web_elem.text or "").strip() if web_elem is not None else ""
    except Exception as e:
        log.warning("info.xml 읽기 실패 (%s): %s", info_path, e)
        return None
    return parse_kakao_web_url(web_url)


def thumbnail_cache_dir(database_path: str) -> Path:
    """"웹툰 전체목록" 카드 썸네일을 받아 저장해두는 곳 — DB 파일이 있는 곳(영구 볼륨)에 둬서 컨테이너를
    재시작해도 다시 안 받는다. 지워도 필요할 때 다시 받아서 채워지므로 언제 지워도 안전하다."""
    return Path(database_path).parent / "kakao_cover_cache"


def _to_jpeg_bytes(raw: bytes) -> bytes | None:
    """받은 이미지를 JPEG 바이트로 맞춘다(이미 JPEG면 재인코딩 없이 그대로). PNG/WebP의 투명
    배경은 흰색으로 채운다 — 그냥 RGB로 바꾸면 투명한 곳이 검게 나오기 때문."""
    if not _PIL_AVAILABLE:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if image.format == "JPEG":
                return raw
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (255, 255, 255))
                flattened.paste(rgba, mask=rgba.split()[3])
            else:
                flattened = image.convert("RGB")
            buffer = io.BytesIO()
            flattened.save(buffer, "JPEG", quality=95)
            return buffer.getvalue()
    except Exception as e:
        log.warning("표지 이미지 변환 실패: %s", e)
        return None


def fetch_official_cover_bytes(
    series_id: str, *, filename: str = kakao_api.IMAGE_FILENAME_ORIGINAL, timeout: int = 15
) -> bytes | None:
    """카카오페이지 공식 표지를 받아 JPEG 바이트로 반환한다(실패 시 None). 회차 목록 응답의
    series_item.thumbnail로 표지 kid를 얻은 뒤 그 이미지를 내려받는다. filename으로 원본("o1", 기본)
    이나 줄인 크기(목록 카드용)를 고른다. 아카이빙(폴더에 직접 써야 함)과 "웹툰 전체목록" 썸네일
    (파일로 저장해서 서빙해야 함)처럼 저장 방식이 다른 호출부가 같이 쓴다."""
    try:
        response = requests.get(
            kakao_api.KAKAO_PRODUCT_LIST_URL,
            params=kakao_api.product_list_params(int(series_id)),
            headers=kakao_api.HEADERS,
            timeout=timeout,
        )
        if response.status_code != 200:
            log.warning("카카오페이지 표지 조회 실패 (series_id=%s): HTTP %s", series_id, response.status_code)
            return None
        kid = kakao_api.thumbnail_kid(response.json())
        if not kid:
            log.warning("카카오페이지 표지 정보 없음 (series_id=%s)", series_id)
            return None
        image = requests.get(kakao_api.image_url(kid, filename), headers=kakao_api.IMAGE_HEADERS, timeout=timeout)
        if image.status_code != 200:
            log.warning("카카오페이지 표지 이미지 받기 실패 (series_id=%s): HTTP %s", series_id, image.status_code)
            return None
    except Exception as e:
        log.warning("카카오페이지 표지 받기 예외 (series_id=%s): %s", series_id, e)
        return None
    return _to_jpeg_bytes(image.content)


def refresh_kakao_cover_if_applicable(webtoon_dir: Path, *, timeout: int = 15) -> bool:
    """webtoon_dir가 카카오페이지 작품 폴더면(info.xml의 <Web> 태그로 판단) cover.jpg를 공식 표지로
    교체한다. 카카오페이지가 아니거나(옛 카카오웹툰 주소 포함), 받는 중 뭐가 됐든 실패하면 아무것도
    건드리지 않고 False를 반환한다 — 이 실패가 아카이빙 자체를 막으면 안 되므로, 호출부(archiver.py)는
    이 결과와 무관하게 항상 이동을 계속 진행한다."""
    series_id = find_kakao_series_id_for_folder(webtoon_dir)
    if series_id is None:
        return False

    jpeg_bytes = fetch_official_cover_bytes(series_id, timeout=timeout)
    if jpeg_bytes is None:
        return False

    try:
        # 확장자가 뭐였든(cover.png 등) 표지는 항상 JPEG로 저장되므로, 다른 확장자의 옛 커버가
        # 남아있으면 같이 지워서 cover.*가 두 개 이상 안 남게 한다.
        for old in webtoon_dir.glob("cover.*"):
            if old.name != "cover.jpg":
                old.unlink(missing_ok=True)
        (webtoon_dir / "cover.jpg").write_bytes(jpeg_bytes)
    except OSError as e:
        log.warning("카카오 표지 저장 실패 (%s): %s", webtoon_dir, e)
        return False
    return True
