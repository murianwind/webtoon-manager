"""
카카오웹툰 표지(cover.jpg) 합성 — 사용자가 제공한 webtoon_metadata.py의 합성
로직을 그대로 이식했다. 배경(backgroundImage) 위에 캐릭터(featuredCharacterImage,
투명 배경)를 겹치고, 세로로 매우 긴 원본을 3:4로 상단 기준 크롭한 뒤, 하단
그라데이션 + 제목 로고(titleImage)를 얹는 3단 구조.

완결 아카이빙(keep_last=False) 시에만 archiver.py에서 호출된다 — 주기/수동
부분이동에서는 안 쓴다(합성이 네트워크+이미지 처리가 있는 무거운 작업이라).

info.xml/커버가 아예 없는 폴더에는 적용할 방법이 없다(이 폴더가 카카오인지
자체를 <Web> 태그로 판단하기 때문) — 그런 폴더는 그냥 건너뛴다.
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from PIL import Image, ImageDraw

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

log = logging.getLogger(__name__)

KAKAO_DETAIL_URL_TMPL = "https://gateway-kw.kakao.com/decorator/v2/decorator/contents/{content_id}"

# 실제 사이트 카드 디자인 값 그대로 재현 (webtoon_metadata.py에서 HAR로 확인한 고정값)
KAKAO_TITLE_LOGO_WIDTH_RATIO = 0.55
KAKAO_TITLE_LOGO_BOTTOM_MARGIN_RATIO = 0.06
KAKAO_GRADIENT_COLOR = (84, 83, 83)
KAKAO_GRADIENT_HEIGHT_RATIO = 0.5
KAKAO_GRADIENT_STOPS = ((0.0, 0.0), (0.3304, 0.5), (0.6609, 0.9), (1.0, 1.0))
KAKAO_COVER_ASPECT_RATIO = 3 / 4


def parse_kakao_web_url(url: str) -> str | None:
    """info.xml의 <Web> URL이 카카오웹툰이면 content_id를, 아니면(네이버 등) None을 반환한다."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "webtoon.kakao.com" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if segments and segments[-1].isdigit():
        return segments[-1]
    return None


def find_kakao_content_id_for_folder(webtoon_dir: Path) -> str | None:
    """이 폴더의 info.xml <Web> 태그를 읽어서 카카오면 content_id를, 아니면 None을 반환한다.
    info.xml이 없거나 읽을 수 없으면 조용히 None (카카오 여부를 알 방법이 없으니
    건드리지 않는 게 안전)."""
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


def _first_nonempty(*values) -> str:
    for v in values:
        if v:
            return str(v)
    return ""


def _append_image_extensions(base_url: str) -> list[str]:
    """확장자 없는 카카오 CDN 이미지 URL에 .webp -> .png -> .jpg 순으로 시도한다."""
    if not base_url:
        return []
    if base_url.lower().endswith((".webp", ".png", ".jpg", ".jpeg")):
        return [base_url]
    return [base_url + ext for ext in (".webp", ".png", ".jpg")]


def _fetch_asset_bytes(session: requests.Session, base_url: str, timeout: int) -> bytes | None:
    for url in _append_image_extensions(base_url):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            continue
    return None


def fetch_kakao_content_detail(content_id: str, timeout: int = 15) -> dict | None:
    """카카오 상세 API를 조회해서 합성에 필요한 소재(배경/캐릭터/제목로고 URL)를 뽑는다."""
    try:
        resp = requests.get(KAKAO_DETAIL_URL_TMPL.format(content_id=content_id), timeout=timeout)
        if resp.status_code != 200:
            log.warning("카카오 상세 조회 실패 (content_id=%s): HTTP %s", content_id, resp.status_code)
            return None
        data = (resp.json() or {}).get("data") or {}
    except Exception as e:
        log.warning("카카오 상세 조회 예외 (content_id=%s): %s", content_id, e)
        return None

    if not data:
        return None
    return {
        "background": _first_nonempty(data.get("backgroundImage")),
        "character": _first_nonempty(data.get("featuredCharacterImageB"), data.get("featuredCharacterImageA")),
        "title_logo": _first_nonempty(data.get("titleImageB"), data.get("titleImageA")),
        "background_color": _first_nonempty(data.get("backgroundColor")) or "#ffffff",
    }


def _gradient_alpha_at(position: float) -> float:
    stops = KAKAO_GRADIENT_STOPS
    for (pos0, alpha0), (pos1, alpha1) in zip(stops, stops[1:]):
        if pos0 <= position <= pos1:
            if pos1 == pos0:
                return alpha1
            ratio = (position - pos0) / (pos1 - pos0)
            return alpha0 + (alpha1 - alpha0) * ratio
    return stops[-1][1]


def _build_bottom_gradient(width: int, height: int):
    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        position = y / max(1, height - 1)
        alpha = max(0, min(255, round(_gradient_alpha_at(position) * 255)))
        draw.line([(0, y), (width, y)], fill=(*KAKAO_GRADIENT_COLOR, alpha))
    return gradient


def _crop_to_aspect_from_top(image, aspect_ratio: float):
    width, height = image.size
    target_height = round(width / aspect_ratio)
    if target_height >= height:
        return image
    return image.crop((0, 0, width, target_height))


def compose_kakao_cover(
    background_bytes: bytes | None, character_bytes: bytes | None, title_logo_bytes: bytes | None, background_color: str
) -> bytes | None:
    """배경+캐릭터 합성 -> 3:4 상단 크롭 -> 하단 그라데이션 -> 제목 로고. 최종 JPEG 바이트 반환."""
    if not _PIL_AVAILABLE:
        log.warning("Pillow가 설치되어 있지 않아 카카오 표지를 합성할 수 없습니다.")
        return None
    if not background_bytes and not character_bytes:
        return None

    try:
        background = Image.open(io.BytesIO(background_bytes)).convert("RGBA") if background_bytes else None
        character = Image.open(io.BytesIO(character_bytes)).convert("RGBA") if character_bytes else None

        if background is not None and character is not None:
            canvas_size = background.size if background.size[0] >= character.size[0] else character.size
        elif background is not None:
            canvas_size = background.size
        else:
            canvas_size = character.size

        if background is None:
            background = Image.new("RGBA", canvas_size, background_color)
        elif background.size != canvas_size:
            background = background.resize(canvas_size)
        if character is not None and character.size != canvas_size:
            character = character.resize(canvas_size)

        composed = Image.alpha_composite(background, character) if character else background
        composed = _crop_to_aspect_from_top(composed, KAKAO_COVER_ASPECT_RATIO)

        gradient_height = int(composed.height * KAKAO_GRADIENT_HEIGHT_RATIO)
        if gradient_height > 0:
            gradient = _build_bottom_gradient(composed.width, gradient_height)
            composed.paste(gradient, (0, composed.height - gradient_height), gradient)

        if title_logo_bytes:
            try:
                title_logo = Image.open(io.BytesIO(title_logo_bytes)).convert("RGBA")
                target_width = max(1, int(composed.width * KAKAO_TITLE_LOGO_WIDTH_RATIO))
                scale = target_width / title_logo.width
                target_height = max(1, int(title_logo.height * scale))
                title_logo = title_logo.resize((target_width, target_height))
                x = (composed.width - title_logo.width) // 2
                margin_bottom = int(composed.height * KAKAO_TITLE_LOGO_BOTTOM_MARGIN_RATIO)
                y = composed.height - title_logo.height - margin_bottom
                composed.paste(title_logo, (x, y), title_logo)
            except Exception as e:
                log.warning("제목 로고 합성 실패, 배경+캐릭터만 사용: %s", e)

        flattened = Image.new("RGB", composed.size, background_color)
        flattened.paste(composed, mask=composed.split()[3])
        buf = io.BytesIO()
        flattened.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except Exception as e:
        log.warning("카카오 표지 합성 실패: %s", e)
        return None


def refresh_kakao_cover_if_applicable(webtoon_dir: Path, *, timeout: int = 15) -> bool:
    """webtoon_dir가 카카오웹툰 폴더면(info.xml의 <Web> 태그로 판단) cover.jpg를
    새로 합성해서 덮어쓴다. 카카오가 아니거나, 조회/합성 중 뭐가 됐든 실패하면
    아무것도 건드리지 않고 False를 반환한다 — 이 실패가 아카이빙 자체를 막으면
    안 되므로, 호출부(archiver.py)는 이 결과와 무관하게 항상 이동을 계속 진행한다."""
    content_id = find_kakao_content_id_for_folder(webtoon_dir)
    if content_id is None:
        return False

    detail = fetch_kakao_content_detail(content_id, timeout=timeout)
    if detail is None:
        return False

    session = requests.Session()
    background_bytes = _fetch_asset_bytes(session, detail["background"], timeout) if detail["background"] else None
    character_bytes = _fetch_asset_bytes(session, detail["character"], timeout) if detail["character"] else None
    title_logo_bytes = _fetch_asset_bytes(session, detail["title_logo"], timeout) if detail["title_logo"] else None

    jpeg_bytes = compose_kakao_cover(background_bytes, character_bytes, title_logo_bytes, detail["background_color"])
    if jpeg_bytes is None:
        return False

    try:
        # 확장자가 뭐였든(cover.png 등) 카카오 표지는 항상 JPEG로 합성되므로, 다른
        # 확장자의 옛 커버가 남아있으면 같이 지워서 cover.*가 두 개 이상 안 남게 한다.
        for old in webtoon_dir.glob("cover.*"):
            if old.name != "cover.jpg":
                old.unlink(missing_ok=True)
        (webtoon_dir / "cover.jpg").write_bytes(jpeg_bytes)
    except OSError as e:
        log.warning("카카오 표지 저장 실패 (%s): %s", webtoon_dir, e)
        return False
    return True
