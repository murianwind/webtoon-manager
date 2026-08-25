"""
구독 중인 웹툰을 스캔해서
  1) 완결/휴재 여부, 장르·태그를 갱신하고
  2) "등록된 작가"(watched_authors, enabled=1)의 다른 신작을 자동 구독에 추가하고
  3) "등록된 태그"(watched_tags, enabled=1)에 새로 편입된 작품을 자동 구독에 추가한다.

작가/태그는 구독 여부와 분리된 별도 레지스트리(watched_authors/watched_tags)로 관리한다.
구독 중인 웹툰의 작가는 스캔할 때 자동으로 레지스트리에 등록되지만(enabled=1 기본값),
사용자가 설정 페이지에서 명시적으로 끄면 그 작가의 신작은 더 이상 자동추가되지 않는다
(구독 자체는 유지된 채로) — 이미 있는 항목의 enabled는 upsert가 덮어쓰지 않는다.

웹툰 하나 처리 중 예외가 나도 전체 스캔은 계속되도록 개별 웹툰 단위로 예외를 격리한다.
"""

import asyncio
import logging

import aiohttp

from app import job_status, naver_api, repository
from app.config import Settings
from app.discord_notify import send_webhook_notification
from app.models import TitleInfo

log = logging.getLogger(__name__)

_ARTIST_SCAN_CONCURRENCY = 5

DEFAULT_TAG_IDS_AND_NAMES = {"134": "", "133": ""}


def ensure_default_tags_seeded() -> None:
    """watched_tags 테이블이 비어있으면(최초 실행) 기본 태그를 채운다. 이후엔 건드리지 않는다."""
    if repository.list_watched_tags():
        return
    for tag_id in DEFAULT_TAG_IDS_AND_NAMES:
        repository.upsert_watched_tag(tag_id, "", enabled=True)


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


def _matches_enabled_writer(other: dict, enabled_author_ids: set[str]) -> bool:
    other_writer_ids = {str(w.get("id")) for w in (other.get("author") or {}).get("writers") or []}
    return bool(enabled_author_ids & other_writer_ids)


async def _is_other_finished(
    session: aiohttp.ClientSession, other: dict, other_title_id: str, settings: Settings
) -> bool:
    for key in ("finished", "finish"):
        if key in other:
            return bool(other[key])
    info = await naver_api.fetch_title_info(session, other_title_id, settings.request_timeout_seconds)
    return bool(info and info.is_finished)


async def search_authors_by_name(
    session: aiohttp.ClientSession, name: str, settings: Settings
) -> list[dict]:
    """네이버 통합검색으로 작가 이름을 찾는다 (검색 결과에 작가 id/이름이 직접 들어있음)."""
    results = await naver_api.search_webtoons(session, name, settings.request_timeout_seconds)

    found: dict[str, dict] = {}
    for item in results:
        for author_id, author_name in item.author_ids_names:
            if name in author_name and author_id not in found:
                found[author_id] = {
                    "author_id": author_id,
                    "author_name": author_name,
                    "sample_title": item.title_name,
                    "sample_title_id": item.title_id,
                }
    return list(found.values())


async def backfill_missing_thumbnails(session: aiohttp.ClientSession, settings: Settings) -> int:
    """
    상태(구독중/구독해제/제외됨)와 무관하게, 썸네일 URL이 아직 없는 모든 웹툰의
    썸네일을 채운다. 신작 자동추가 같은 부수효과는 없이 정보 조회만 한다 —
    그래서 목록제외된 웹툰도 여기서는 대상에 포함된다.
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
        repository.update_genres_and_tags(title_id, info.genres, info.tags)
        repository.update_is_paused(title_id, info.is_paused)
        filled += 1

    return filled


async def enrich_one(session: aiohttp.ClientSession, title_id: str, settings: Settings) -> tuple[bool, str]:
    """
    구독(또는 목록제외) 직후, 혹은 재동기화 때 실행 — 정보(썸네일/장르/태그/휴재/작가)를
    채우고 작가를 레지스트리에 등록한다. (성공 여부, 사람이 읽을 결과 메시지)를 반환한다 —
    예전엔 정보 조회 실패를 그냥 조용히 넘어가서 "재동기화는 성공했다는데 왜 목록이
    비어있지?"를 진단할 방법이 없었다.
    """
    try:
        info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
    except Exception as e:
        return False, f"정보 조회 예외: {e}"

    if info is None:
        return False, "정보 조회 실패 (네이버 API 응답 없음/오류)"

    repository.update_is_adult(title_id, info.is_adult)
    repository.update_thumbnail_url(title_id, info.thumbnail_url)
    repository.update_genres_and_tags(title_id, info.genres, info.tags)
    repository.update_is_paused(title_id, info.is_paused)
    if info.writer_ids:
        repository.update_writer_ids(title_id, sorted(info.writer_ids))

    registered_names = []
    for writer_id, writer_name in info.writer_id_name_pairs:
        repository.upsert_watched_author(writer_id, writer_name, enabled=True)
        registered_names.append(writer_name or writer_id)

    if registered_names:
        return True, f"작가 등록: {', '.join(registered_names)}"
    return True, "작가 정보 없음 (API 응답에 작가 필드가 비어있음)"


async def resync_registry(session: aiohttp.ClientSession, settings: Settings) -> int:
    """
    지금 추적 중인(구독중/구독해제) 모든 웹툰을 한 번에 훑어서 작가/태그 레지스트리를
    채운다. enrich_one은 구독하는 "그 순간"에만 동작하는데, 그 기능이 생기기 전에
    이미 구독해뒀던 웹툰들은 정기 스캔이 돌기 전까지 계속 비어있는 문제가 있어서
    — 설정 페이지에서 사용자가 바로 눌러서 즉시 채울 수 있게 하는 수동 트리거다.

    각 웹툰의 처리 결과(성공/실패 이유)를 job_status 로그에 남겨서, "재동기화는
    끝났다는데 왜 목록이 비었지?"를 실행 이력에서 바로 진단할 수 있게 한다.
    """
    targets = [wt for wt in repository.list_all() if wt.status != repository.STATUS_EXCLUDED]
    if not targets:
        job_status.log_line("registry", "재동기화 대상 웹툰이 없습니다 (구독중/구독해제된 것이 없음)")
        return 0

    semaphore = asyncio.Semaphore(_ARTIST_SCAN_CONCURRENCY)
    registered_count = 0

    async def _run_one(wt) -> bool:
        nonlocal registered_count
        async with semaphore:
            success, message = await enrich_one(session, wt.title_id, settings)
            job_status.log_line("registry", f"[{wt.title}] {message}")
            await asyncio.sleep(settings.delay_seconds)
            return success and message.startswith("작가 등록")

    results = await asyncio.gather(*(_run_one(wt) for wt in targets))
    registered_count = sum(1 for r in results if r)
    return registered_count


async def scan_subscriptions_for_updates(session: aiohttp.ClientSession, settings: Settings) -> None:
    """구독 중인(status=active) 모든 웹툰의 완결/휴재/장르 갱신 + 등록된 작가 신작 자동추가."""
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
        repository.update_genres_and_tags(title_id, info.genres, info.tags)
        repository.update_is_paused(title_id, info.is_paused)

        if info.writer_ids:
            repository.update_writer_ids(title_id, sorted(info.writer_ids))

        # 구독중인 작품의 작가는 자동으로 레지스트리에 등록된다 (이미 있으면 enabled는 안 건드림).
        for writer_id, writer_name in info.writer_id_name_pairs:
            repository.upsert_watched_author(writer_id, writer_name, enabled=True)

        enabled_author_ids = repository.get_enabled_author_ids()

        for other in others:
            other_title_id = str(other.get("titleId", ""))
            if not other_title_id or repository.exists(other_title_id):
                continue
            if not _matches_enabled_writer(other, enabled_author_ids):
                continue  # 등록 해제된 작가이거나 그림작가만 겹치는 작품은 제외
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
    """등록된(watched_tags, enabled=1) 태그(큐레이션)에 새로 편입된 완결되지 않은 작품을 자동 구독에 추가한다."""
    tag_ids = repository.get_enabled_tag_ids()
    new_entry_lines: list[str] = []

    for tag_id in tag_ids:
        try:
            tag_name = await naver_api.fetch_curation_title_name(
                session, int(tag_id), settings.request_timeout_seconds
            )
            repository.upsert_watched_tag(tag_id, tag_name, enabled=True)
            await asyncio.sleep(settings.delay_seconds)
            items = await naver_api.fetch_curation_titles(
                session, int(tag_id), settings.request_timeout_seconds, settings.delay_seconds
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
