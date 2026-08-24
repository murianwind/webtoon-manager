"""
주기 작업 정의.

세 가지 독립적인 주기로 나눈다 (기존 hermes 크론의 3분리 구조를 그대로 계승):
  - discovery_job   : 완결 감지 + 작가/태그 신작 자동추가 (네이버 API 다건 호출, 좀 무거움)
  - download_job    : 구독 중인 웹툰의 새 회차 다운로드 + 압축 + info.xml 생성
  - commands_job    : 디스코드 완결-확인 스레드의 사용자 명령만 확인 (가볍고 자주 돌려도 안전)

각 잡은 웹툰 하나 처리 중 예외가 나도 다른 웹툰 처리를 막지 않도록 individually try/except.
"""

import logging
from pathlib import Path

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import naver_api, repository, tracker
from app.comicinfo import download_cover_image, write_comicinfo_file
from app.config import Settings, get_settings
from app.cookie_loader import get_adult_cookies
from app.discord_notify import sync_completion_thread
from app.downloader import download_webtoon_episodes
from app.file_utils import remove_forbidden_str
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)


async def _download_new_episodes_for_one(
    session: aiohttp.ClientSession, settings: Settings, title_id: str
) -> None:
    webtoon = repository.get(title_id)
    if webtoon is None or webtoon.status != repository.STATUS_ACTIVE:
        return

    info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
    if info is None:
        return

    cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
    if info.is_adult and not cookies:
        log.warning("성인 웹툰(titleId=%s)이지만 인증 쿠키가 없어 건너뜁니다.", title_id)
        return

    all_episodes = await naver_api.fetch_all_episodes(
        session, title_id, cookies or {}, settings.request_timeout_seconds
    )
    free_episodes = naver_api.free_episodes_only(all_episodes)
    new_episodes = [ep for ep in free_episodes if ep.episode_no > webtoon.last_downloaded_no]
    if not new_episodes:
        return

    log.info("다운로드 시작: %s (titleId=%s) — %d개 회차", info.title_name, title_id, len(new_episodes))
    results = await download_webtoon_episodes(
        title_id=title_id,
        title_name=info.title_name,
        webtoon_type=info.webtoon_type,
        episodes=new_episodes,
        cookies=cookies or {},
        download_root=settings.download_root,
        folder_zero_fill=settings.folder_zero_fill,
        image_zero_fill=settings.image_zero_fill,
        batch_size=settings.batch_size,
        max_concurrent_downloads=settings.max_concurrent_downloads,
        delay_seconds=settings.delay_seconds,
        timeout_seconds=settings.request_timeout_seconds,
    )

    # last_downloaded_no는 last_downloaded_no+1부터 "끊기지 않고 연속 성공한" 마지막 화까지만 올린다.
    # 중간에 실패한 화가 있으면 다음 실행 때 그 화부터 다시 시도하게 하기 위함.
    results_by_no = {r.episode_no: r.success for r in results}
    new_last_no = webtoon.last_downloaded_no
    expected_no = webtoon.last_downloaded_no + 1
    while results_by_no.get(expected_no):
        new_last_no = expected_no
        expected_no += 1

    if new_last_no > webtoon.last_downloaded_no:
        repository.update_last_downloaded_no(title_id, new_last_no)

        safe_title = remove_forbidden_str(info.title_name)
        webtoon_dir = Path(settings.download_root) / safe_title
        zip_episode_folders(webtoon_dir)
        write_comicinfo_file(webtoon_dir, info)
        await download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
        log.info("다운로드 완료: %s (titleId=%s) — %d화까지", info.title_name, title_id, new_last_no)

    failed = [r.episode_no for r in results if not r.success]
    if failed:
        log.warning("다운로드 실패 회차 (titleId=%s): %s", title_id, failed)


async def run_download_job() -> None:
    settings = get_settings()
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE)
    async with aiohttp.ClientSession() as session:
        for webtoon in active_webtoons:
            try:
                await _download_new_episodes_for_one(session, settings, webtoon.title_id)
            except Exception as e:
                log.error("웹툰(titleId=%s) 다운로드 처리 중 예외 — 다음 웹툰으로 진행: %s", webtoon.title_id, e)


async def run_discovery_job() -> None:
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        try:
            await tracker.scan_subscriptions_for_updates(session, settings)
        except Exception as e:
            log.error("작가 기반 신작 스캔 중 예외: %s", e)
        try:
            await tracker.scan_curation_tags(session, settings)
        except Exception as e:
            log.error("태그 기반 신작 스캔 중 예외: %s", e)


async def run_commands_job() -> None:
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        try:
            await sync_completion_thread(session, settings)
        except Exception as e:
            log.error("디스코드 명령 처리 중 예외: %s", e)


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_discovery_job, "interval", minutes=settings.scan_interval_minutes, id="discovery_job"
    )
    scheduler.add_job(
        run_download_job, "interval", minutes=settings.download_interval_minutes, id="download_job"
    )
    scheduler.add_job(
        run_commands_job, "interval", minutes=settings.commands_only_interval_minutes, id="commands_job"
    )
    return scheduler
