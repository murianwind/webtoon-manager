"""
카카오페이지 로그인 쿠키 관리. 브라우저에서 Cookie-Editor 같은 확장으로 page.kakao.com 쿠키를 JSON으로
export한 것을 설정 화면에 붙여넣으면, 검증해서 암호화해 저장한다(보통 약 30일 유지 — 만료되면 다시
export해서 교체). 쿠키 값은 어디에도(로그, 화면, 백업) 내보내지 않는다.

만료는 두 가지로 알린다: (1) 실제로 요청해보니 로그인이 풀려 있을 때, (2) 쿠키 파일에 적힌 만료일이 며칠
안 남았을 때. 같은 알림은 24시간에 한 번만 보낸다.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone

from app import crypto, discord_notify, repository

log = logging.getLogger(__name__)

# 사용자가 확인해준 로그인에 필요한 쿠키(필수 6개 + 선택 1개)
REQUIRED_COOKIES = ("_kau", "_kpwtkn", "_T_ANO", "_kahai", "_kawlt", "_kpdid")
# 있으면 알아보고(요청에 같이 보내고) 없으면 무시하는 쿠키 — 없어도 로그인 검사/만료 계산에 영향이 없다
OPTIONAL_COOKIES = ("_karmt",)
SETTING_KEY = "kakao_page_cookies"
_ALERT_EXPIRED_KEY = "kakao_page_alert_expired_at"
_ALERT_SOON_KEY = "kakao_page_alert_soon_at"
_ALERT_INTERVAL_SECONDS = 24 * 3600
_EXPIRY_WARNING_DAYS = 5


def parse_cookie_export(text: str) -> list[dict]:
    """Cookie-Editor JSON을 검증해서 카카오 도메인 쿠키만 골라 돌려준다. 잘못됐으면 ValueError(사용자에게
    그대로 보여줄 한국어 메시지 — 쿠키 값은 메시지에 넣지 않는다)."""
    if not (text or "").strip():
        raise ValueError("붙여넣은 내용이 비어 있습니다.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("JSON 형식이 아닙니다. Cookie-Editor에서 Export → JSON으로 복사한 내용을 그대로 붙여넣어 주세요.") from None
    if not isinstance(data, list):
        raise ValueError("쿠키 목록(JSON 배열)이 아닙니다. Cookie-Editor의 Export → JSON 결과를 붙여넣어 주세요.")
    if not data:
        raise ValueError("쿠키 목록이 비어 있습니다. page.kakao.com에서 로그인한 상태로 export한 내용을 붙여넣어 주세요.")

    cookies = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
            continue
        if "kakao" not in str(item.get("domain", "")):
            continue  # 카카오와 무관한 쿠키는 보관하지 않는다
        cookies.append(
            {
                "name": item["name"], "value": str(item["value"]), "domain": item.get("domain", ""),
                "path": item.get("path", "/"), "expirationDate": item.get("expirationDate"),
            }
        )
    if not cookies:
        raise ValueError("카카오 쿠키가 하나도 없습니다. page.kakao.com에서 로그인한 상태로 export했는지 확인해주세요.")
    missing = [n for n in REQUIRED_COOKIES if n not in {c["name"] for c in cookies}]
    if missing:
        raise ValueError(f"필수 쿠키가 빠져 있습니다: {', '.join(missing)}. 로그인한 상태에서 다시 export해주세요.")
    return cookies


def save_cookies(cookies: list[dict]) -> None:
    repository.set_setting(SETTING_KEY, crypto.encrypt(json.dumps(cookies)))


def load_cookies() -> list[dict] | None:
    raw = repository.get_setting(SETTING_KEY)
    if not raw:
        return None
    try:
        return json.loads(crypto.decrypt(raw))
    except Exception as e:  # 암호화 키가 바뀌었거나 손상된 경우 — 저장 안 된 것처럼 다룬다
        log.warning("카카오페이지 쿠키를 읽지 못했습니다: %s", type(e).__name__)
        return None


def delete_cookies() -> None:
    repository.set_setting(SETTING_KEY, None)


def update_cookie_values(new_values: dict[str, str]) -> None:
    """서버가 로그인 유지를 위해 새로 내려준 쿠키 값을 저장된 쿠키에 반영한다(이름이 같은 것만 값 교체)."""
    cookies = load_cookies()
    if not cookies:
        return
    for cookie in cookies:
        if cookie["name"] in new_values:
            cookie["value"] = new_values[cookie["name"]]
    save_cookies(cookies)


def cookie_map(cookies: list[dict]) -> dict[str, str]:
    return {c["name"]: c["value"] for c in cookies}


def cookie_status(cookies: list[dict] | None, *, now: float | None = None) -> dict:
    """화면에 보여줄 상태(쿠키 값은 절대 안 담는다). 만료일은 필수 쿠키 중 가장 빨리 만료되는 것 기준."""
    if not cookies:
        return {"saved": False, "missing": list(REQUIRED_COOKIES), "optional_present": [], "expires_at": None, "days_left": None}
    now = time.time() if now is None else now
    names = {c["name"] for c in cookies}
    expiries = [c["expirationDate"] for c in cookies if c["name"] in REQUIRED_COOKIES and c.get("expirationDate")]
    earliest = min(expiries) if expiries else None
    return {
        "saved": True,
        "missing": [n for n in REQUIRED_COOKIES if n not in names],
        "optional_present": [n for n in OPTIONAL_COOKIES if n in names],
        "expires_at": datetime.fromtimestamp(earliest, tz=timezone.utc).isoformat() if earliest else None,
        "days_left": math.floor((earliest - now) / 86400) if earliest else None,
    }


def health_alert_message(logged_in: bool | None, days_left: int | None) -> str | None:
    """알려야 할 상황이면 디스코드 문구를, 아니면 None. logged_in이 None(네트워크 문제/차단 등으로 모름)이면
    로그아웃으로 단정하지 않는다."""
    if logged_in is False:
        return (
            "🍪 **카카오페이지 쿠키 만료** — 로그인이 풀렸습니다\n"
            "브라우저에서 page.kakao.com에 다시 로그인한 뒤, Cookie-Editor로 쿠키를 JSON으로 export해서 "
            "설정 화면에 다시 붙여넣어 주세요. 그때까지 카카오페이지 다운로드는 건너뜁니다."
        )
    if logged_in is True and days_left is not None and days_left <= _EXPIRY_WARNING_DAYS:
        return (
            f"🍪 **카카오페이지 로그인 쿠키가 곧 만료됩니다** (약 {max(days_left, 0)}일 남음)\n"
            "만료되기 전에 Cookie-Editor로 쿠키를 다시 export해서 설정 화면에 붙여넣어 주세요."
        )
    return None


def _recently_sent(key: str) -> bool:
    raw = repository.get_setting(key)
    if not raw:
        return False
    try:
        return time.time() - float(raw) < _ALERT_INTERVAL_SECONDS
    except ValueError:
        return False


async def notify_if_needed(session, settings, logged_in: bool | None, days_left: int | None) -> bool:
    """필요하면 디스코드로 알린다(같은 종류는 24시간에 한 번). 실제로 보냈으면 True."""
    if logged_in is True:
        repository.set_setting(_ALERT_EXPIRED_KEY, None)  # 다시 정상이면, 다음에 또 풀렸을 때 바로 알릴 수 있게
    message = health_alert_message(logged_in, days_left)
    if message is None:
        return False
    key = _ALERT_EXPIRED_KEY if logged_in is False else _ALERT_SOON_KEY
    if _recently_sent(key):
        return False
    await discord_notify.send_webhook_notification(session, settings, message)
    repository.set_setting(key, str(time.time()))
    log.warning("카카오페이지 쿠키 알림 전송: %s", "로그인 풀림" if logged_in is False else "곧 만료")
    return True
