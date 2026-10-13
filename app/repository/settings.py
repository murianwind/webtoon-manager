"""앱 설정 값(settings 테이블)과 그 위에 얹은 켜고 끄는 설정들.
"""

from app.db import fetchone, write_transaction

# ── settings (key-value) ──────────────────────────────────────────

def get_setting(key: str) -> str | None:
    row = fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with write_transaction() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def is_author_auto_register_enabled() -> bool:
    """구독(자동 구독 포함) 시 그 작품 작가를 '등록된 작가'(자동 신작추가 대상)로 자동 등록할지 여부. 값이 명시적으로 '0'일 때만
    꺼짐 — 기존 사용자는 값이 아예 없을 테니 켜짐 유지."""
    return get_setting("auto_register_author_on_subscribe") != "0"


KAKAO_RENTAL_AUTO_USE_KEY = "kakao_rental_ticket_auto_use"


def is_kakao_rental_ticket_auto_use_enabled() -> bool:
    """자동 다운로드에서 보유한 일반 대여권을 써서 기다무로 못 여는 회차를 열지 여부. 값이 명시적으로 '1'일 때만 켜짐(기본 꺼짐) —
    대여권은 돈을 내고 산 것이라 사용자가 직접 켠 경우에만 쓴다."""
    return get_setting(KAKAO_RENTAL_AUTO_USE_KEY) == "1"
