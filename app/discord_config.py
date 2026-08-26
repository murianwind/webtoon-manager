"""
디스코드 설정(웹훅 URL / 봇 토큰 / 알림 채널 ID) 저장·조회.

env var 폴백 없이 DB에서만 관리한다 — 웹 설정 페이지가 유일한 입력 경로다.
저장 시 암호화(crypto.py)해서 DB 파일 자체에는 평문이 남지 않는다.
봇 토큰은 빈 값으로 저장 요청이 오면 기존 값을 유지한다 (입력창을 비워두면
"안 바꿈"으로 취급 — 토큰을 다시 입력해야만 바뀐다).
"""

from app import repository
from app.crypto import decrypt, encrypt

_KEY_WEBHOOK_URL = "discord_webhook_url"
_KEY_BOT_TOKEN = "discord_bot_token"
_KEY_NOTIFY_CHANNEL_ID = "discord_notify_channel_id"


def get_webhook_url() -> str:
    return decrypt(repository.get_setting(_KEY_WEBHOOK_URL) or "")


def get_bot_token() -> str:
    return decrypt(repository.get_setting(_KEY_BOT_TOKEN) or "")


def get_notify_channel_id() -> str:
    return decrypt(repository.get_setting(_KEY_NOTIFY_CHANNEL_ID) or "")


def set_webhook_url(value: str) -> None:
    repository.set_setting(_KEY_WEBHOOK_URL, encrypt(value) if value else None)


def set_bot_token(value: str) -> None:
    """빈 문자열이면 기존 값을 그대로 둔다 (토큰을 실수로 지우지 않도록)."""
    if value:
        repository.set_setting(_KEY_BOT_TOKEN, encrypt(value))


def set_notify_channel_id(value: str) -> None:
    repository.set_setting(_KEY_NOTIFY_CHANNEL_ID, encrypt(value) if value else None)


def is_bot_configured() -> bool:
    return bool(get_bot_token() and get_notify_channel_id())
