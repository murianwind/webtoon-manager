"""
디스코드 설정(웹훅 URL / 봇 토큰 / 알림 채널 ID) 실효값 조회.

DB(settings 테이블)에 사용자가 웹에서 저장한 값이 있으면 그걸 쓰고, 없으면
.env의 기본값을 쓴다. 봇 토큰은 빈 값으로 저장 요청이 오면 기존 값을 유지한다
(입력창을 비워두면 "안 바꿈"으로 취급 — 토큰을 다시 입력해야만 바뀐다).
"""

from app import repository
from app.config import get_settings

_KEY_WEBHOOK_URL = "discord_webhook_url"
_KEY_BOT_TOKEN = "discord_bot_token"
_KEY_NOTIFY_CHANNEL_ID = "discord_notify_channel_id"


def get_webhook_url() -> str:
    return repository.get_setting(_KEY_WEBHOOK_URL) or get_settings().webtoon_webhook_url


def get_bot_token() -> str:
    return repository.get_setting(_KEY_BOT_TOKEN) or get_settings().webtoon_bot_token


def get_notify_channel_id() -> str:
    return repository.get_setting(_KEY_NOTIFY_CHANNEL_ID) or get_settings().webtoon_notify_channel_id


def set_webhook_url(value: str) -> None:
    repository.set_setting(_KEY_WEBHOOK_URL, value or None)


def set_bot_token(value: str) -> None:
    """빈 문자열이면 기존 값을 그대로 둔다 (토큰을 실수로 지우지 않도록)."""
    if value:
        repository.set_setting(_KEY_BOT_TOKEN, value)


def set_notify_channel_id(value: str) -> None:
    repository.set_setting(_KEY_NOTIFY_CHANNEL_ID, value or None)


def is_bot_configured() -> bool:
    return bool(get_bot_token() and get_notify_channel_id())


def is_webhook_configured() -> bool:
    return bool(get_webhook_url())
