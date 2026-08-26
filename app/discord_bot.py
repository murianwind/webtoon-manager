"""
완결 확인용 디스코드 Gateway 봇.

REST 폴링 대신 discord.py로 Gateway(웹소켓)에 상시 연결해서 버튼 클릭을
실시간으로 받는다. 설정(토큰/채널ID)은 discord_config를 통해 DB 우선으로 읽는다
— 설정이 없으면 조용히 비활성 상태로 남는다 (에러 아님).

구독해제/알람제외는 API를 거치지 않고 같은 프로세스 안에서 repository 함수를
직접 호출한다 (같은 컨테이너 안이라 별도 HTTP 왕복이 필요 없음).
"""

import asyncio
import logging

import discord

from app import discord_config, repository

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

        try:
            if custom_id.startswith(_CUSTOM_ID_UNSUBSCRIBE_PREFIX):
                title_id = custom_id[len(_CUSTOM_ID_UNSUBSCRIBE_PREFIX):]
                await self._handle_unsubscribe(interaction, title_id)
            elif custom_id.startswith(_CUSTOM_ID_ACKNOWLEDGE_PREFIX):
                title_id = custom_id[len(_CUSTOM_ID_ACKNOWLEDGE_PREFIX):]
                await self._handle_acknowledge(interaction, title_id)
        except Exception as e:
            # 여기서 안 잡으면 사용자는 "상호작용 실패"만 보고 원인을 알 방법이 없다.
            log.error("완결 확인 버튼 처리 중 예외 (custom_id=%s): %s", custom_id, e)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass  # 알림 시도 자체가 실패해도 봇이 죽으면 안 됨

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

    async def send_plain_message(self, content: str) -> None:
        channel = self.get_channel(self.notify_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.notify_channel_id)
        await channel.send(content=content)


def is_ready() -> bool:
    return _client is not None and _client.is_ready()


async def start_bot() -> None:
    global _client, _run_task

    if not discord_config.is_bot_configured():
        log.info("디스코드 봇 토큰/채널ID 미설정 — 완결 확인 봇 비활성")
        return

    try:
        channel_id = int(discord_config.get_notify_channel_id())
    except ValueError:
        log.error("알림 채널ID가 숫자가 아닙니다: %s", discord_config.get_notify_channel_id())
        return

    _client = CompletionBotClient(notify_channel_id=channel_id)
    _run_task = asyncio.create_task(_client.start(discord_config.get_bot_token()))
    _run_task.add_done_callback(_log_bot_task_failure)
    log.info("완결 확인 디스코드 봇 시작 중...")


def _log_bot_task_failure(task: asyncio.Task) -> None:
    """봇 연결은 백그라운드 태스크라 실패해도 아무도 안 기다리므로, 조용히 사라지지
    않게 예외를 명시적으로 로그에 남긴다 (토큰이 틀렸을 때 등)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("완결 확인 봇 연결 실패: %s", exc)


async def stop_bot() -> None:
    global _client, _run_task
    if _client is not None:
        await _client.close()
        _client = None
    if _run_task is not None:
        _run_task.cancel()
        _run_task = None


async def restart_bot() -> None:
    """설정(토큰/채널ID)이 바뀐 뒤 호출 — 기존 연결을 끊고 새 설정으로 다시 연결한다."""
    await stop_bot()
    await start_bot()


async def send_completion_prompt(title_id: str, title: str) -> None:
    if not is_ready():
        log.warning("완결 확인 봇이 아직 준비되지 않아 알림을 보내지 못했습니다 (titleId=%s)", title_id)
        return
    try:
        await _client.send_completion_prompt(title_id, title)
    except Exception as e:
        log.error("완결 알림 전송 실패 (titleId=%s): %s", title_id, e)


async def send_test_message() -> tuple[bool, str]:
    """설정 페이지의 '봇 테스트' 버튼에서 호출. (성공 여부, 사용자에게 보여줄 메시지)."""
    if not discord_config.is_bot_configured():
        return False, "봇 토큰/채널ID가 설정되지 않았습니다."
    if not is_ready():
        return False, "봇이 아직 연결되지 않았습니다 (설정 저장 직후라면 몇 초 후 다시 시도해주세요)."
    try:
        await _client.send_plain_message("✅ 완결 확인 봇 테스트 메시지입니다. 정상적으로 연결되어 있습니다.")
        return True, "테스트 메시지를 전송했습니다."
    except Exception as e:
        return False, f"전송 실패: {e}"
