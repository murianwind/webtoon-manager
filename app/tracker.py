"""
구독 중인 웹툰을 스캔해서
  1) 완결 여부를 갱신하고
  2) 같은 글작가의 다른 신작을 자동 구독에 추가하고
  3) 지정된 태그(큐레이션)에 새로 편입된 작품을 자동 구독에 추가한다.

기존 webtoon_manager.py의 스캔 로직을 그대로 옮기되, 파일 대신 SQLite(repository)를
데이터 소스로 쓴다. 웹툰 하나 처리 중 예외가 나도 전체 스캔은 계속되도록
개별 웹툰 단위로 예외를 격리한다.
"""

import asyncio
import logging

import aiohttp

from app import naver_api, repository
from app.config import Settings
from app.discord_notify import send_webhook_notification
from app.models import TitleInfo

log = logging.getLogger(__name__)

_ARTIST_SCAN_CONCURRENCY = 5


async def _fetch_info_and_others(
    session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, title_id: str, settings: Settings
) -> tuple[str, TitleInfo | None, list[dict]]:
    async with semaphore:
        try:
            info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
            others: list[dict] = []
            if info:
                others = await naver_api.fetch_other_titles_by_artist(
                    session, title_id, settings.request_timeout_seconds
                )
            await asyncio.sleep(settings.delay_seconds)
            return title_id, info, others
        except Exception as e:
            log.error("웹툰(titleId=%s) 스캔 중 예외 — 이 항목만 건너뜁니다: %s", title_id, e)
            return title_id, None, []


def _matches_by_writer(other: dict, writer_ids: set[str]) -> bool:
    if not writer_ids:
        return False
    other_writer_ids = {str(w.get("id")) for w in (other.get("author") or {}).get("writers") or []}
    return bool(writer_ids & other_writer_ids)


async def _is_other_finished(
    session: aiohttp.ClientSession, other: dict, other_title_id: str, settings: Settings
) -> bool:
    for key in ("finished", "finish"):
        if key in other:
            return bool(other[key])
    info = await naver_api.fetch_title_info(session, other_title_id, settings.request_timeout_seconds)
    return bool(info and info.is_finished)


async def backfill_missing_thumbnails(session: aiohttp.ClientSession, settings: Settings) -> int:
    """
    상태(구독중/구독해제/제외됨)와 무관하게, 썸네일 URL이 아직 없는 모든 웹툰의
    썸네일을 채운다. 신작 자동추가 같은 부수효과는 없이 정보 조회만 한다 —
    그래서 목록제외된 웹툰도 여기서는 대상에 포함된다 (원래 신작 스캔은 구독중만
    보기 때문에 제외된 웹툰은 이 백필이 없으면 썸네일을 영영 못 채운다).
    """
    targets = [wt for wt in repository.list_all() if not wt.thumbnail_url]
    if not targets:
        return 0

    semaphore = asyncio.Semaphore(_ARTIST_SCAN_CONCURRENCY)

    async def _fetch_one(title_id: str) -> tuple[str, TitleInfo | None]:
        async with semaphore:
            try:
                info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
                await asyncio.sleep(settings.delay_seconds)
                return title_id, info
            except Exception as e:
                log.error("썸네일 백필 중 예외 (titleId=%s): %s", title_id, e)
                return title_id, None

    results = await asyncio.gather(*(_fetch_one(wt.title_id) for wt in targets))

    filled = 0
    for title_id, info in results:
        if info is None or not info.thumbnail_url:
            continue
        repository.update_thumbnail_url(title_id, info.thumbnail_url)
        repository.update_is_adult(title_id, info.is_adult)
        filled += 1

    return filled


async def scan_subscriptions_for_updates(session: aiohttp.ClientSession, settings: Settings) -> None:
    """구독 중인(status=active) 모든 웹툰의 완결 여부 갱신 + 작가 신작 자동추가."""
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE)
    if not active_webtoons:
        return

    semaphore = asyncio.Semaphore(_ARTIST_SCAN_CONCURRENCY)
    tasks = [
        _fetch_info_and_others(session, semaphore, wt.title_id, settings) for wt in active_webtoons
    ]
    fetch_results = await asyncio.gather(*tasks)

    new_entry_lines: list[str] = []

    for title_id, info, others in fetch_results:
        if info is None:
            continue

        if info.is_finished:
            repository.mark_finished(title_id)

        repository.update_is_adult(title_id, info.is_adult)
        repository.update_thumbnail_url(title_id, info.thumbnail_url)

        if info.writer_ids:
            repository.update_writer_ids(title_id, sorted(info.writer_ids))

        for other in others:
            other_title_id = str(other.get("titleId", ""))
            if not other_title_id or repository.exists(other_title_id):
                continue
            if not _matches_by_writer(other, info.writer_ids):
                continue  # 그림작가만 겹치는 작품은 제외 (글작가 기준만 인정)
            if await _is_other_finished(session, other, other_title_id, settings):
                continue  # 이미 완결된 작품은 신규로 추가하지 않음

            other_title_name = other.get("titleName", "")
            repository.upsert_new(
                title_id=other_title_id,
                title=other_title_name,
                added_source=repository.SOURCE_ARTIST,
            )
            new_entry_lines.append(f"- {other_title_name} (`{other_title_id}`)")

    if new_entry_lines:
        message = "🆕 **작가 신작 자동 추가**\n" + "\n".join(new_entry_lines)
        await send_webhook_notification(session, settings, message)


async def scan_curation_tags(session: aiohttp.ClientSession, settings: Settings) -> None:
    """설정된 태그(큐레이션)에 새로 편입된 완결되지 않은 작품을 자동 구독에 추가한다."""
    new_entry_lines: list[str] = []

    for tag_id in settings.tag_ids:
        try:
            tag_name = await naver_api.fetch_curation_title_name(
                session, tag_id, settings.request_timeout_seconds
            )
            await asyncio.sleep(settings.delay_seconds)
            items = await naver_api.fetch_curation_titles(
                session, tag_id, settings.request_timeout_seconds, settings.delay_seconds
            )
        except Exception as e:
            log.error("태그(tag_id=%s) 스캔 중 예외 — 이 태그만 건너뜁니다: %s", tag_id, e)
            continue

        for item in items:
            other_title_id = str(item.get("titleId", ""))
            if not other_title_id or repository.exists(other_title_id):
                continue
            if item.get("finished"):
                continue

            other_title_name = item.get("titleName", "")
            repository.upsert_new(
                title_id=other_title_id,
                title=other_title_name,
                added_source=repository.SOURCE_TAG,
            )
            new_entry_lines.append(f"- {other_title_name} (`{other_title_id}`) [태그: {tag_name}]")
            log.info("태그 '%s'(id=%s)에서 신작 발견: %s (%s)", tag_name, tag_id, other_title_name, other_title_id)

    if new_entry_lines:
        message = "🆕 **태그 신작 자동 추가**\n" + "\n".join(new_entry_lines)
        await send_webhook_notification(session, settings, message)
