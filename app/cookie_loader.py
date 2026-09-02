"""
성인 웹툰 인증용 NID_AUT / NID_SES 쿠키를 브라우저 export 파일에서 읽어온다.

지원 포맷: {"cookies": [{"name": ..., "domain": ..., "value": ...}, ...], "origins": [...]}
(Playwright storage_state() 표준 포맷. chokobo_murian.json도 이 포맷이다.)

이 파일 하나에 네이버 외 수백 개 사이트의 쿠키/로컬스토리지가 섞여 있을 수 있으므로,
domain에 'naver.com'이 포함된 항목 중 이름이 일치하는 것만 추출하고 나머지는 무시한다.
파일 경로는 설정(config.cookie_file_path)에서 오며 코드에 하드코딩하지 않는다.
"""

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_TARGET_COOKIE_NAMES = ("NID_AUT", "NID_SES")


def load_naver_auth_cookies(cookie_file_path: str) -> dict[str, str]:
    """
    성공 시 {"NID_AUT": "...", "NID_SES": "..."} 형태로 반환.
    파일이 없거나 필요한 쿠키를 찾지 못하면 빈 dict를 반환한다(크래시하지 않음) —
    성인 웹툰이 아닌 작품 다운로드에는 애초에 필요 없는 값이라 상위 로직에서
    "없으면 인증 없이 진행" 식으로 자연스럽게 처리되도록 한다.
    """
    path = Path(cookie_file_path)
    if not path.is_file():
        log.warning("쿠키 파일을 찾을 수 없습니다: %s", cookie_file_path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("쿠키 파일을 읽는 중 오류 발생 (%s): %s", cookie_file_path, e)
        return {}

    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        log.error("쿠키 파일 형식이 예상과 다릅니다 (cookies 배열 없음): %s", cookie_file_path)
        return {}

    found: dict[str, str] = {}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        domain = cookie.get("domain", "") or ""
        if name in _TARGET_COOKIE_NAMES and "naver.com" in domain:
            value = cookie.get("value")
            if value:
                found[name] = value

    missing = [n for n in _TARGET_COOKIE_NAMES if n not in found]
    if missing:
        log.info("쿠키 파일에서 다음 값을 찾지 못했습니다: %s (성인 웹툰 다운로드 시 인증 실패할 수 있음)", missing)

    return found


def get_adult_cookies(cookie_file_path: str) -> Optional[dict[str, str]]:
    """NID_AUT/NID_SES가 둘 다 있을 때만 dict를 반환, 하나라도 없으면 None."""
    cookies = load_naver_auth_cookies(cookie_file_path)
    if "NID_AUT" in cookies and "NID_SES" in cookies:
        return cookies
    return None
