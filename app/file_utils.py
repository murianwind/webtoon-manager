"""
윈도우 파일시스템 금지문자 치환 등 경로/파일명 관련 유틸.

기존 NWebtoon_Downloader의 module/file_processor.py 로직을 그대로 포팅했다.
새 폴더 네이밍 규칙을 만들지 않고 기존 결과물과 100% 동일한 이름이 나오도록
치환 테이블과 순서를 원본과 동일하게 유지한다 (요구사항: 네이밍 규칙 유지).
"""

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
# 카카오웹툰은 이 앱이 직접 다운로드하지 않고, 사용자가 쓰는 별도 도구가 저장한다.
# 그 도구는 ':'을 (이 앱이 네이버 다운로드에 쓰는) 전각 콜론(：)이 아니라 밑줄(_)로
# 치환해서 폴더를 만든다(실제로 확인됨 — 예: "도굴왕: 엔드라인" → "도굴왕_ 엔드라인").
# 그래서 카카오 작품을 뷰어 서버에서 찾을 땐 이 표를 따로 써야 한다. 콜론 외
# 다른 금지문자(*, ?, " 등)에 대해서는 그 도구의 실제 치환 방식이 확인된 게 없어서,
# 일단 네이버와 같은 치환을 그대로 쓴다 — 다른 문자에서도 문제가 확인되면 그때
# 마찬가지로 이 표만 따로 고치면 된다.
_FORBIDDEN_CHAR_TABLE_TO_KAKAO = FORBIDDEN_CHAR_TABLE_TO.replace("：", "_")
_FORBIDDEN_TABLE_KAKAO = str.maketrans(FORBIDDEN_CHAR_TABLE_FROM, _FORBIDDEN_CHAR_TABLE_TO_KAKAO)


def _soft_strip_edges(text: str) -> str:
    return text.strip(_TRIM_CHARS)


def remove_forbidden_str(name: str) -> str:
    """폴더/파일명에 쓸 수 없는 문자를 눈으로 보기엔 비슷한 유니코드 문자로 치환한다.
    이 앱이 직접 다운로드하는(네이버) 폴더명 규칙 — 카카오는 remove_forbidden_str_kakao
    참고."""
    processed = name.translate(_FORBIDDEN_TABLE)
    processed = processed.translate(_WHITESPACE_TABLE)
    processed = _soft_strip_edges(processed)
    return processed.strip().rstrip(".")


def remove_forbidden_str_kakao(name: str) -> str:
    """카카오웹툰을 받는 별도 도구의 폴더명 규칙 — 콜론만 밑줄로 치환되는 것만
    다르고 나머지는 remove_forbidden_str와 동일하게 처리한다."""
    processed = name.translate(_FORBIDDEN_TABLE_KAKAO)
    processed = processed.translate(_WHITESPACE_TABLE)
    processed = _soft_strip_edges(processed)
    return processed.strip().rstrip(".")


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
