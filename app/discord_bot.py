"""
완결 확인용 디스코드 Gateway 봇.

기존 REST 폴링 + 텍스트 명령 스레드 방식 대신, discord.py로 Gateway(웹소켓)에
상시 연결해서 버튼 클릭을 실시간으로 받는다. "확인 주기"라는 개념 자체가 없어진다 —
사용자가 몇 초 뒤에 누르든 몇 시간 뒤에 누르든 즉시 처리된다.

WEBTOON_BOT_TOKEN / WEBTOON_NOTIFY_CHANNEL_ID가 설정되어 있을 때만 동작하고,
없으면 조용히 비활성 상태로 남는다 (에러 아님).

구독해제/알람제외는 API를 거치지 않고 같은 프로세스 안에서 repository 함수를
직접 호출한다 (같은 컨테이너 안이라 별도 HTTP 왕복이 필요 없음).
"""

import asyncio
import logging

import discord

from app import repository
from app.config import Settings

log = logging.getLogger(__name__)

_CUSTOM_ID_UNSUBSCRIBE_PREFIX = "webtoon_unsubscribe:"
_CUSTOM_ID_ACKNOWLEDGE_PREFIX = "webtoon_ack:"

_client: "CompletionBotClient | None" = None
_run_task: asyncio.Task | None = None


class CompletionBotClient(discord.Client):
    def __init__(self, notify_channel_id: int):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.notify_channel_id = notify_channel_id

    async def on_ready(self) -> None:
        log.info("완결 확인 디스코드 봇 연결됨: %s", self.user)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")

        if custom_id.startswith(_CUSTOM_ID_UNSUBSCRIBE_PREFIX):
            title_id = custom_id[len(_CUSTOM_ID_UNSUBSCRIBE_PREFIX):]
            await self._handle_unsubscribe(interaction, title_id)
        elif custom_id.startswith(_CUSTOM_ID_ACKNOWLEDGE_PREFIX):
            title_id = custom_id[len(_CUSTOM_ID_ACKNOWLEDGE_PREFIX):]
            await self._handle_acknowledge(interaction, title_id)

    async def _handle_unsubscribe(self, interaction: discord.Interaction, title_id: str) -> None:
        webtoon = await asyncio.to_thread(repository.get, title_id)
        if webtoon is None:
            await interaction.response.send_message("이미 처리된 웹툰입니다.", ephemeral=True)
            return
        await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)
        await interaction.response.edit_message(
            content=f"✅ **{webtoon.title}** 구독해제했습니다.", view=None
        )

    async def _handle_acknowledge(self, interaction: discord.Interaction, title_id: str) -> None:
        webtoon = await asyncio.to_thread(repository.get, title_id)
        if webtoon is None:
            await interaction.response.send_message("이미 처리된 웹툰입니다.", ephemeral=True)
            return
        await asyncio.to_thread(repository.acknowledge_finish, title_id)
        await interaction.response.edit_message(
            content=f"🔕 **{webtoon.title}** 완결 알림을 껐습니다 (구독은 유지됩니다).", view=None
        )

    async def send_completion_prompt(self, title_id: str, title: str) -> None:
        channel = self.get_channel(self.notify_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.notify_channel_id)

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="구독해제",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_CUSTOM_ID_UNSUBSCRIBE_PREFIX}{title_id}",
            )
        )
        view.add_item(
            discord.ui.Button(
                label="알람 제외",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_CUSTOM_ID_ACKNOWLEDGE_PREFIX}{title_id}",
            )
        )
        await channel.send(content=f"📗 **{title}** 완결되었습니다. 구독을 해제할까요?", view=view)


def is_enabled(settings: Settings) -> bool:
    return bool(settings.webtoon_bot_token and settings.webtoon_notify_channel_id)


async def start_bot(settings: Settings) -> None:
    global _client, _run_task

    if not is_enabled(settings):
        log.info("WEBTOON_BOT_TOKEN / WEBTOON_NOTIFY_CHANNEL_ID 미설정 — 완결 확인 봇 비활성")
        return

    try:
        channel_id = int(settings.webtoon_notify_channel_id)
    except ValueError:
        log.error("WEBTOON_NOTIFY_CHANNEL_ID가 숫자가 아닙니다: %s", settings.webtoon_notify_channel_id)
        return

    _client = CompletionBotClient(notify_channel_id=channel_id)
    _run_task = asyncio.create_task(_client.start(settings.webtoon_bot_token))
    log.info("완결 확인 디스코드 봇 시작 중...")


async def stop_bot() -> None:
    global _client, _run_task
    if _client is not None:
        await _client.close()
        _client = None
    if _run_task is not None:
        _run_task.cancel()
        _run_task = None


async def send_completion_prompt(title_id: str, title: str) -> None:
    if _client is None or not _client.is_ready():
        log.warning("완결 확인 봇이 아직 준비되지 않아 알림을 보내지 못했습니다 (titleId=%s)", title_id)
        return
    try:
        await _client.send_completion_prompt(title_id, title)
    except Exception as e:
        log.error("완결 알림 전송 실패 (titleId=%s): %s", title_id, e)
