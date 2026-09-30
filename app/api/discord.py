"""디스코드 설정(웹훅/봇 토큰/채널)과 테스트 발송."""


import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app import (
    discord_bot,
    discord_config,
    discord_notify,
)


log = logging.getLogger(__name__)

router = APIRouter()



class DiscordSettingsOut(BaseModel):
    webhook_url_set: bool
    bot_token_set: bool
    notify_channel_id: str
    bot_ready: bool


class DiscordSettingsIn(BaseModel):
    webhook_url: str = ""
    bot_token: str = ""  # 비워두면 기존 값 유지
    notify_channel_id: str = ""


@router.get("/settings/discord", response_model=DiscordSettingsOut)
async def get_discord_settings():
    return DiscordSettingsOut(
        webhook_url_set=bool(await asyncio.to_thread(discord_config.get_webhook_url)),
        bot_token_set=bool(await asyncio.to_thread(discord_config.get_bot_token)),
        notify_channel_id=await asyncio.to_thread(discord_config.get_notify_channel_id),
        bot_ready=discord_bot.is_ready(),
    )


@router.post("/settings/discord", response_model=DiscordSettingsOut)
async def update_discord_settings(payload: DiscordSettingsIn):
    await asyncio.to_thread(discord_config.set_webhook_url, payload.webhook_url)
    await asyncio.to_thread(discord_config.set_bot_token, payload.bot_token)
    await asyncio.to_thread(discord_config.set_notify_channel_id, payload.notify_channel_id)
    await discord_bot.restart_bot()
    return await get_discord_settings()


@router.post("/settings/discord/test-webhook")
async def test_discord_webhook():
    success, message = await discord_notify.send_test_webhook_message()
    return {"success": success, "message": message}


@router.post("/settings/discord/test-bot")
async def test_discord_bot():
    success, message = await discord_bot.send_test_message()
    return {"success": success, "message": message}
