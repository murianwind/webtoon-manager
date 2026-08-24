"""
윈도우 파일시스템 금지문자 치환 등 경로/파일명 관련 유틸.

기존 NWebtoon_Downloader의 module/file_processor.py 로직을 그대로 포팅했다.
새 폴더 네이밍 규칙을 만들지 않고 기존 결과물과 100% 동일한 이름이 나오도록
치환 테이블과 순서를 원본과 동일하게 유지한다 (요구사항: 네이밍 규칙 유지).
"""

import re

from app.constants import FORBIDDEN_CHAR_TABLE_FROM, FORBIDDEN_CHAR_TABLE_TO

_WINDOWS_WEIRD_SPACES = [
    "\u00a0",  # NO-BREAK SPACE
    "\u200b",  # ZERO WIDTH SPACE
    "\u2009",  # THIN SPACE
    "\u200a",  # HAIR SPACE
    "\u3000",  # IDEOGRAPHIC SPACE
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
]
_TRIM_CHARS = " \t\r\n" + "".join(_WINDOWS_WEIRD_SPACES)
_FORBIDDEN_TABLE = str.maketrans(FORBIDDEN_CHAR_TABLE_FROM, FORBIDDEN_CHAR_TABLE_TO)
_WHITESPACE_TABLE = str.maketrans("\t\n", "  ")


def _soft_strip_edges(text: str) -> str:
    return text.strip(_TRIM_CHARS)


def remove_forbidden_str(name: str) -> str:
    """폴더/파일명에 쓸 수 없는 문자를 눈으로 보기엔 비슷한 유니코드 문자로 치환한다."""
    processed = name.translate(_FORBIDDEN_TABLE)
    processed = processed.translate(_WHITESPACE_TABLE)
    processed = _soft_strip_edges(processed)
    return processed.strip().rstrip(".")


def remove_html_tags(text: str) -> str:
    cleaner = re.compile(r"<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});")
    return re.sub(cleaner, "", text)


def episode_folder_name(episode_no: int, subtitle: str, folder_zero_fill: int) -> str:
    """다운로드 시 회차 폴더명: '[0001] 부제목' 형식 (기존 규칙과 동일)."""
    safe_subtitle = remove_forbidden_str(subtitle)
    return f"[{str(episode_no).zfill(folder_zero_fill)}] {safe_subtitle}"


def image_file_name(image_index_1_based: int, image_zero_fill: int, ext: str) -> str:
    return f"{str(image_index_1_based).zfill(image_zero_fill)}{ext}"


def guess_image_extension(img_url: str) -> str:
    last_segment = img_url.split("/")[-1]
    if "." in last_segment:
        return "." + last_segment.split(".")[-1].split("?")[0]
    return ".jpg"
