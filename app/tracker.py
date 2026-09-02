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
from pathlib import Path

import aiohttp

from app import comicinfo, discord_notify, job_status, kakao_api, naver_api, repository
from app.file_utils import remove_forbidden_str
from app.config import Settings
from app.discord_notify import send_webhook_notification
from app.models import TitleInfo

log = logging.getLogger(__name__)

DEFAULT_TAG_IDS_AND_NAMES = {"134": "", "133": ""}


def ensure_default_tags_seeded() -> None:
    """watched_tags 테이블이 비어있으면(최초 실행) 기본 태그를 채운다 — 단, 꺼진
    상태(enabled=False)로만 채운다. 예전엔 켜진 상태로 심어서 사용자가 요청한 적
    없는 태그가 자동으로 활성화되어 있었고, 그 결과 원치 않는 작품이 자동구독
    돼버리는 문제가 실제로 있었다 — 사용자가 "작가/태그 관리"에서 원하면 직접
    켜도록, 기본은 항상 꺼진 채로 등록만 해둔다."""
    if repository.list_watched_tags():
        return
    for tag_id in DEFAULT_TAG_IDS_AND_NAMES:
        repository.upsert_watched_tag(tag_id, "", enabled=False)


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

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)

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
        repository.update_is_new(title_id, info.is_new)
        filled += 1

    return filled


async def enrich_one(
    session: aiohttp.ClientSession, title_id: str, settings: Settings, register_authors_enabled: bool = True
) -> tuple[bool, str]:
    """
    구독(또는 목록제외) 직후, 혹은 재동기화 때 실행 — 정보(썸네일/장르/태그/휴재/작가)를
    채우고 작가를 레지스트리에 등록한다. (성공 여부, 사람이 읽을 결과 메시지)를 반환한다 —
    예전엔 정보 조회 실패를 그냥 조용히 넘어가서 "재동기화는 성공했다는데 왜 목록이
    비어있지?"를 진단할 방법이 없었다.

    register_authors_enabled=False로 부르면 작가는 등록하되 "등록된 작가"(자동 신작추가
    대상)로는 올리지 않는다 — 목록제외한 웹툰이나, 아직 구독 안 한 웹툰의 저자를
    "전체 작가 목록" 채우기 용도로 알아낼 때 쓴다.
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
    repository.update_is_new(title_id, info.is_new)
    if info.writer_id_name_pairs:
        ids, names = zip(*info.writer_id_name_pairs)
        repository.update_writer_ids_and_names(title_id, list(ids), list(names))

    registered_names = []
    for writer_id, writer_name in info.writer_id_name_pairs:
        repository.upsert_watched_author(writer_id, writer_name, enabled=register_authors_enabled)
        registered_names.append(writer_name or writer_id)

    if registered_names:
        return True, f"작가 등록: {', '.join(registered_names)}"
    return True, "작가 정보 없음 (API 응답에 작가 필드가 비어있음)"


async def scan_kakao_authors_for_new_titles(session: aiohttp.ClientSession, settings) -> int:
    """
    등록된 카카오웹툰 작가(이름 기준) 각각을 검색해서, 그 작가의 작품 중 이번에 처음
    보는 것(kakao_seen_titles에 없는 title_id)이 있으면 디스코드로 알린다.

    최초로 어떤 작가를 등록한 직후 첫 스캔에서는, 그 작가의 기존 작품 전체가
    "새로 발견됨"으로 잡혀서 전부 알림이 가버린다 — 그래서 그 작가를 이번에 처음
    스캔하는 거라면(seen 목록이 비어있으면) 지금 있는 작품 전체를 기준선으로만
    저장하고 알림은 보내지 않는다(다운로드 리포트 기능과 동일한 패턴).
    """
    authors = [a for a in repository.list_watched_authors(platform="kakao") if a.enabled]
    if not authors:
        return 0

    new_titles_found = 0
    for author in authors:
        try:
            results = await kakao_api.search_by_author(session, author.author_name, settings.request_timeout_seconds)
        except Exception as e:
            log.error("카카오 작가 검색 중 예외 (author=%s): %s", author.author_name, e)
            job_status.log_line("discovery", f"[카카오/{author.author_name}] 검색 오류: {e}")
            continue

        already_seen = repository.get_seen_kakao_title_ids(author.author_name)
        is_first_scan = len(already_seen) == 0

        for item in results:
            if item["title_id"] in already_seen:
                continue
            repository.add_seen_kakao_title(author.author_name, item["title_id"], item["title_name"])
            if is_first_scan:
                continue  # 기준선 저장만, 알림 없음

            new_titles_found += 1
            message = (
                f"🆕 **카카오웹툰 신작 발견**\n"
                f"작가: {author.author_name}\n"
                f"제목: {item['title_name']}\n"
                f"ID: {item['title_id']}"
            )
            try:
                await discord_notify.send_webhook_notification(session, settings, message)
            except Exception as e:
                log.error("카카오 신작 알림 전송 실패: %s", e)
            job_status.log_line("discovery", f"[카카오/{author.author_name}] 신작 발견: {item['title_name']} (id={item['title_id']})")

        await asyncio.sleep(settings.delay_seconds)

    return new_titles_found


async def sync_metadata_for_all(settings) -> int:
    """
    추적 중인(구독중/구독해제/제외됨 전부) 웹툰 폴더를 스캔해서, info.xml이나
    cover.jpg가 없는 것만 다시 만들어준다. 폴더가 아예 없으면(다운로드한 적 없는
    웹툰) 건너뛴다. 한 웹툰 처리 중 예외가 나도(디스크 오류, 커버 이미지 네트워크
    실패 등) 전체가 멈추지 않고 다음 웹툰으로 넘어가야 하므로, 다른 스캔 함수들과
    동일하게 웹툰 단위로 예외를 격리한다.
    """
    targets = repository.list_all()
    fixed = 0
    async with aiohttp.ClientSession() as session:
        for wt in targets:
            try:
                safe_title = remove_forbidden_str(wt.title)
                webtoon_dir = Path(settings.download_root) / safe_title
                if not webtoon_dir.is_dir():
                    continue
                if not comicinfo.needs_comicinfo(webtoon_dir):
                    continue

                info = TitleInfo(
                    title_id=wt.title_id,
                    title_name=wt.title,
                    synopsis="",
                    is_adult=wt.is_adult,
                    webtoon_type="webtoon",
                    is_finished=wt.is_finished,
                    thumbnail_url=wt.thumbnail_url,
                    genres=wt.genres,
                    tags=wt.tags,
                )
                comicinfo.write_comicinfo_file(webtoon_dir, info)
                if wt.thumbnail_url:
                    await comicinfo.download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
                job_status.log_line("metadata_sync", f"[{wt.title}] info.xml / 커버 이미지 생성")
                fixed += 1
            except Exception as e:
                log.error("메타 동기화 중 예외 (titleId=%s) — 다음 웹툰으로 진행: %s", wt.title_id, e)
                job_status.log_line("metadata_sync", f"[{wt.title}] 처리 중 오류: {e}")
    return fixed


async def refresh_inactive_metadata(session: aiohttp.ClientSession, settings: Settings) -> int:
    """
    구독중(active)이 아닌(구독해제/제외됨) 웹툰들의 정보(태그/장르/휴재/신작 등)를
    가볍게 갱신한다. 신작 스캔(scan_subscriptions_for_updates)은 구독중인 것만
    처리하기 때문에, 구독해제/제외됨은 사용자가 수동으로 "지금 전체 재동기화"를
    누르지 않는 한 계속 예전 정보(빈 태그, 신작 여부 등)로 남아있는 문제가 있었다 —
    이걸 신작 스캔이 돌 때마다 자동으로 같이 처리해서, 수동 재동기화가 항상 필요하지
    않도록 한다. 저자를 "등록된 작가"로 올리지는 않는다(구독한 게 아니므로).
    """
    targets = [wt for wt in repository.list_all() if wt.status != repository.STATUS_ACTIVE]
    if not targets:
        return 0

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)

    async def _run_one(wt) -> bool:
        async with semaphore:
            success, _message = await enrich_one(session, wt.title_id, settings, register_authors_enabled=False)
            await asyncio.sleep(settings.delay_seconds)
            return success

    results = await asyncio.gather(*(_run_one(wt) for wt in targets))
    return sum(1 for r in results if r)


async def resync_registry(session: aiohttp.ClientSession, settings: Settings) -> int:
    """
    지금 추적 중인(구독중/구독해제) 모든 웹툰을 한 번에 훑어서 작가/태그 레지스트리를
    채운다. enrich_one은 구독하는 "그 순간"에만 동작하는데, 그 기능이 생기기 전에
    이미 구독해뒀던 웹툰들은 정기 스캔이 돌기 전까지 계속 비어있는 문제가 있어서
    — 설정 페이지에서 사용자가 바로 눌러서 즉시 채울 수 있게 하는 수동 트리거다.

    각 웹툰의 처리 결과(성공/실패 이유)를 job_status 로그에 남겨서, "재동기화는
    끝났다는데 왜 목록이 비었지?"를 실행 이력에서 바로 진단할 수 있게 한다.
    """
    targets = repository.list_all()  # 제외됨도 포함 — 구독 상태와 무관하게 저자/태그 정보는 채워야 함
    if not targets:
        job_status.log_line("registry", "재동기화 대상 웹툰이 없습니다")
        return 0

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)

    async def _run_one(wt) -> bool:
        async with semaphore:
            # 제외된 웹툰의 정보는 채우되(썸네일/장르/태그 등), 그 저자를 "등록된 작가"로
            # 올리진 않는다 — 목록제외한 웹툰의 작가가 관심작가가 되면 안 되기 때문.
            register_authors_enabled = wt.status != repository.STATUS_EXCLUDED
            success, message = await enrich_one(
                session, wt.title_id, settings, register_authors_enabled=register_authors_enabled
            )
            job_status.log_line("registry", f"[{wt.title}] {message}")
            await asyncio.sleep(settings.delay_seconds)
            return success and message.startswith("작가 등록") and register_authors_enabled

    results = await asyncio.gather(*(_run_one(wt) for wt in targets))
    return sum(1 for r in results if r)


async def scan_subscriptions_for_updates(session: aiohttp.ClientSession, settings: Settings) -> None:
    """구독 중인(status=active) 모든 웹툰의 완결/휴재/장르 갱신 + 등록된 작가 신작 자동추가."""
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE)
    if not active_webtoons:
        return

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
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
        repository.update_is_new(title_id, info.is_new)

        if info.writer_id_name_pairs:
            ids, names = zip(*info.writer_id_name_pairs)
            repository.update_writer_ids_and_names(title_id, list(ids), list(names))

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
            await enrich_one(session, other_title_id, settings)  # writer_ids/tags 없이 들어가는 것 방지
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
            await enrich_one(session, other_title_id, settings)  # writer_ids/tags 없이 들어가는 것 방지
            new_entry_lines.append(f"- {other_title_name} (`{other_title_id}`) [태그: {tag_name}]")
            log.info("태그 '%s'(id=%s)에서 신작 발견: %s (%s)", tag_name, tag_id, other_title_name, other_title_id)

    if new_entry_lines:
        message = "🆕 **태그 신작 자동 추가**\n" + "\n".join(new_entry_lines)
        await send_webhook_notification(session, settings, message)
