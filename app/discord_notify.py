"""
신작 자동추가 웹훅 알림.

완결 확인(구독해제/알람제외) 관련 로직은 discord_bot.py의 실시간 Gateway 봇으로
이전되었다 — 예전에는 REST 폴링 + 텍스트 명령 스레드였지만, 이제 discord.py
Gateway 연결로 완결 감지 즉시 버튼 메시지를 보내고 클릭을 바로 처리한다.
"""

import logging

import aiohttp

from app import discord_config
from app.constants import DISCORD_MESSAGE_CHUNK_LIMIT

log = logging.getLogger(__name__)


def _split_message(text: str, limit: int = DISCORD_MESSAGE_CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def send_webhook_notification(session: aiohttp.ClientSession, settings, message: str) -> None:
    webhook_url = discord_config.get_webhook_url()
    if not webhook_url or not message.strip():
        return
    for chunk in _split_message(message):
        try:
            async with session.post(webhook_url, json={"content": chunk}) as resp:
                if resp.status != 204:
                    log.error("웹훅 전송 실패: %s %s", resp.status, await resp.text())
        except Exception as e:
            log.error("웹훅 전송 예외: %s", e)


async def send_test_webhook_message() -> tuple[bool, str]:
    """설정 페이지의 '웹훅 테스트' 버튼에서 호출."""
    webhook_url = discord_config.get_webhook_url()
    if not webhook_url:
        return False, "웹훅 URL이 설정되지 않았습니다."
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url, json={"content": "✅ 웹훅 테스트 메시지입니다. 정상적으로 연결되어 있습니다."}
            ) as resp:
                if resp.status == 204:
                    return True, "테스트 메시지를 전송했습니다."
                return False, f"전송 실패: HTTP {resp.status} — {await resp.text()}"
    except Exception as e:
        return False, f"전송 실패: {e}"
