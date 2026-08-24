"""
디스코드 알림 + 완결 확인 스레드 명령 처리.

기존 webtoon_manager.py의 로직을 그대로 옮기되, in-memory/json 상태 대신
repository의 settings 테이블(스레드 채널/메시지 id)과 webtoons 테이블
(완결 대기 목록 = is_finished=1 AND finish_ack=0)을 사용한다.
"""

import logging

import aiohttp

from app import repository
from app.config import Settings
from app.constants import DISCORD_API, DISCORD_MESSAGE_CHUNK_LIMIT
from app.models import WebtoonRecord

log = logging.getLogger(__name__)

_SETTING_THREAD_CHANNEL_ID = "completion_thread_channel_id"
_SETTING_THREAD_MESSAGE_ID = "completion_thread_message_id"
_SETTING_LAST_SEEN_MESSAGE_ID = "last_seen_message_id"


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


def _bot_headers(settings: Settings) -> dict:
    return {"Authorization": f"Bot {settings.webtoon_bot_token}", "Content-Type": "application/json"}


async def send_webhook_notification(session: aiohttp.ClientSession, settings: Settings, message: str) -> None:
    if not settings.webtoon_webhook_url or not message.strip():
        return
    for chunk in _split_message(message):
        try:
            async with session.post(settings.webtoon_webhook_url, json={"content": chunk}) as resp:
                if resp.status != 204:
                    log.error("웹훅 전송 실패: %s %s", resp.status, await resp.text())
        except Exception as e:
            log.error("웹훅 전송 예외: %s", e)


async def _send_thread_message(session: aiohttp.ClientSession, settings: Settings, channel_id: str, content: str) -> bool:
    if not settings.webtoon_bot_token or not channel_id:
        return False
    for chunk in _split_message(content):
        try:
            async with session.post(
                f"{DISCORD_API}/channels/{channel_id}/messages",
                headers=_bot_headers(settings),
                json={"content": chunk},
            ) as resp:
                if resp.status not in (200, 201):
                    log.error("스레드 메시지 전송 실패: %s %s", resp.status, await resp.text())
                    return False
        except Exception as e:
            log.error("스레드 메시지 전송 예외: %s", e)
            return False
    return True


def _format_pending_list(pending: list[WebtoonRecord]) -> str:
    if not pending:
        return "✅ 현재 대기 중인 완결 웹툰이 없습니다."
    lines = ["📋 **현재 대기 중인 완결 웹툰 목록**"]
    for i, wt in enumerate(pending, 1):
        lines.append(f"{i}. {wt.title} (`{wt.title_id}`)")
    lines.append("\n명령 예시: `삭제 <id>` , `전체삭제` , `제외 <id>` , `목록`")
    return "\n".join(lines)


async def _create_completion_thread(session: aiohttp.ClientSession, settings: Settings) -> bool:
    if not (settings.webtoon_bot_token and settings.webtoon_notify_channel_id):
        log.error("BOT_TOKEN / NOTIFY_CHANNEL_ID 미설정 – 스레드 생성 불가")
        return False
    try:
        async with session.post(
            f"{DISCORD_API}/channels/{settings.webtoon_notify_channel_id}/messages",
            headers=_bot_headers(settings),
            json={
                "content": (
                    "📗 **웹툰 완결 확인 스레드**\n"
                    "아래 목록에서 삭제하거나 제외할 웹툰을 명령으로 알려주세요.\n"
                    "명령 예시:\n"
                    "  삭제 <titleId>          → 해당 웹툰만 삭제\n"
                    "  삭제 <id1> <id2> …      → 여러 개 삭제\n"
                    "  전체삭제               → 대기 중인 모든 웹툰 삭제\n"
                    "  제외 <titleId>         → 완결 알림만 제외 (구독은 유지)\n"
                    "  목록                   → 현재 대기 중인 웹툰 목록을 다시 표시"
                )
            },
        ) as msg_resp:
            if msg_resp.status not in (200, 201):
                log.error("완결 스레드 시작 메시지 생성 실패: %s", await msg_resp.text())
                return False
            message_id = (await msg_resp.json())["id"]

        async with session.post(
            f"{DISCORD_API}/channels/{settings.webtoon_notify_channel_id}/messages/{message_id}/threads",
            headers=_bot_headers(settings),
            json={"name": "웹툰 완결 확인", "auto_archive_duration": 1440},
        ) as thread_resp:
            if thread_resp.status not in (200, 201):
                log.error("완결 스레드 생성 실패: %s", await thread_resp.text())
                return False
            thread_data = await thread_resp.json()

        repository.set_setting(_SETTING_THREAD_CHANNEL_ID, thread_data["id"])
        repository.set_setting(_SETTING_THREAD_MESSAGE_ID, message_id)
        repository.set_setting(_SETTING_LAST_SEEN_MESSAGE_ID, message_id)
        log.info("완결 확인 스레드 생성 성공 (thread_id=%s)", thread_data["id"])
        return True
    except Exception as e:
        log.error("완결 스레드 생성 중 예외: %s", e)
        return False


def _get_pending() -> list[WebtoonRecord]:
    return [
        wt
        for wt in repository.list_by_status(repository.STATUS_ACTIVE)
        if wt.is_finished and not wt.finish_ack
    ]


async def _process_thread_commands(session: aiohttp.ClientSession, settings: Settings) -> None:
    channel_id = repository.get_setting(_SETTING_THREAD_CHANNEL_ID)
    if not channel_id:
        return
    last_seen = int(repository.get_setting(_SETTING_LAST_SEEN_MESSAGE_ID) or 0)

    try:
        async with session.get(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_bot_headers(settings),
            params={"limit": 50},
        ) as resp:
            if resp.status == 404:
                log.warning("완결 확인 스레드(channel_id=%s)를 찾을 수 없어 초기화합니다.", channel_id)
                repository.set_setting(_SETTING_THREAD_CHANNEL_ID, None)
                repository.set_setting(_SETTING_LAST_SEEN_MESSAGE_ID, None)
                return
            if resp.status != 200:
                log.error("스레드 메시지 조회 실패: %s %s", resp.status, await resp.text())
                return
            messages = await resp.json()
    except Exception as e:
        log.error("스레드 명령 처리 중 예외: %s", e)
        return

    new_messages = [m for m in messages if int(m["id"]) > last_seen]
    if not new_messages:
        return
    repository.set_setting(_SETTING_LAST_SEEN_MESSAGE_ID, str(max(int(m["id"]) for m in new_messages)))
    new_messages = list(reversed(new_messages))  # 시간순 정렬

    feedback_lines: list[str] = []
    should_resend_list = False

    for msg in new_messages:
        if (msg.get("author") or {}).get("bot"):
            continue
        if msg.get("type") not in (0, 19, 20):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue

        pending = _get_pending()
        pending_ids = {wt.title_id for wt in pending}

        if content == "전체삭제":
            if pending:
                for wt in pending:
                    repository.set_status(wt.title_id, repository.STATUS_EXCLUDED)
                names = ", ".join(f"{wt.title}(`{wt.title_id}`)" for wt in pending)
                feedback_lines.append(f"🗑️ **전체삭제 완료** — {len(pending)}개: {names}")
                should_resend_list = True
            else:
                feedback_lines.append("⚠️ 전체삭제 — 대기 중인 웹툰이 없습니다.")

        elif content.startswith("삭제"):
            requested_ids = content.split()[1:]
            removed = []
            for title_id in requested_ids:
                if title_id in pending_ids:
                    wt = next(w for w in pending if w.title_id == title_id)
                    repository.set_status(title_id, repository.STATUS_EXCLUDED)
                    removed.append(f"{wt.title}(`{title_id}`)")
                else:
                    feedback_lines.append(f"⚠️ 삭제 — `{title_id}`는 대기 목록에 없습니다.")
            if removed:
                feedback_lines.append(f"🗑️ **삭제 완료** — {len(removed)}개: {', '.join(removed)}")
                should_resend_list = True
            if not requested_ids:
                feedback_lines.append("⚠️ 삭제 — titleId를 함께 입력해주세요. 예: `삭제 772764`")

        elif content.startswith("제외"):
            parts = content.split()
            if len(parts) == 2 and parts[1] in pending_ids:
                title_id = parts[1]
                wt = next(w for w in pending if w.title_id == title_id)
                repository.acknowledge_finish(title_id)
                feedback_lines.append(f"🙈 **제외 완료** — {wt.title}(`{title_id}`) (구독은 유지)")
                should_resend_list = True
            else:
                feedback_lines.append("⚠️ 제외 — 형식: `제외 <titleId>`")

        elif content == "목록":
            should_resend_list = True

        else:
            feedback_lines.append(f"❓ 알 수 없는 명령입니다: `{content}`")

    if feedback_lines:
        await _send_thread_message(session, settings, channel_id, "\n".join(feedback_lines))
    if should_resend_list:
        await _send_thread_message(session, settings, channel_id, _format_pending_list(_get_pending()))


async def sync_completion_thread(session: aiohttp.ClientSession, settings: Settings) -> None:
    """완결 대기 목록이 있으면 스레드를 만들고(없으면 생성), 새 명령을 처리한다."""
    pending = _get_pending()
    channel_id = repository.get_setting(_SETTING_THREAD_CHANNEL_ID)

    if not pending:
        if channel_id:
            repository.set_setting(_SETTING_THREAD_CHANNEL_ID, None)
            repository.set_setting(_SETTING_LAST_SEEN_MESSAGE_ID, None)
        return

    if not channel_id:
        created = await _create_completion_thread(session, settings)
        if created:
            channel_id = repository.get_setting(_SETTING_THREAD_CHANNEL_ID)
            await _send_thread_message(session, settings, channel_id, _format_pending_list(pending))

    if channel_id:
        await _process_thread_commands(session, settings)
