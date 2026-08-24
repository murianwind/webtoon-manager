"""
주기 작업 정의 + 진행상황 로깅 + 설정 기반 동적 주기 조정.

세 가지 독립적인 주기로 나눈다:
  - discovery_job   : 완결 감지 + 작가/태그 신작 자동추가
  - download_job    : 구독 중인 웹툰의 새 회차 다운로드 (회차 하나마다 압축까지 끝내고 다음 화로 진행)
  - commands_job    : 디스코드 완결-확인 스레드의 사용자 명령만 확인

각 잡은 웹툰 하나 처리 중 예외가 나도 다른 웹툰 처리를 막지 않도록 individually try/except.
주기는 DB(settings 테이블)에 사용자가 저장한 값이 있으면 그걸 쓰고, 없으면 환경변수
기본값을 쓴다 — 설정 페이지에서 바꾸면 reschedule_all()로 즉시 반영된다.
"""

import asyncio
import logging
from pathlib import Path

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import comicinfo, job_status, naver_api, repository, tracker
from app.config import Settings, get_settings
from app.cookie_loader import get_adult_cookies
from app.discord_notify import sync_completion_thread
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import count_existing_episode_entries
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)

# DB(settings 테이블)에 저장할 때 쓰는 키. Settings 필드명과 짝지어서 get/set 양쪽에서 공유한다.
INTERVAL_SETTING_KEYS = {
    "scan_interval_minutes": "interval_scan_minutes",
    "download_interval_minutes": "interval_download_minutes",
    "commands_only_interval_minutes": "interval_commands_minutes",
}


def get_effective_interval(field_name: str) -> int:
    settings = get_settings()
    default_value = getattr(settings, field_name)
    override = repository.get_setting(INTERVAL_SETTING_KEYS[field_name])
    if override and override.isdigit() and int(override) >= 1:
        return int(override)
    return default_value


async def _download_new_episodes_for_one(session: aiohttp.ClientSession, settings: Settings, title_id: str) -> None:
    webtoon = repository.get(title_id)
    if webtoon is None or webtoon.status != repository.STATUS_ACTIVE:
        return

    info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
    if info is None:
        job_status.log_line("download", f"[{webtoon.title}] 정보 조회 실패, 건너뜀")
        return

    repository.update_is_adult(title_id, info.is_adult)

    cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
    if info.is_adult and not cookies:
        job_status.log_line("download", f"[{info.title_name}] 성인 웹툰 인증 쿠키 없음, 건너뜀")
        return
    cookies = cookies or {}

    safe_title = remove_forbidden_str(info.title_name)
    webtoon_dir = Path(settings.download_root) / safe_title

    all_episodes = await naver_api.fetch_all_episodes(
        session, title_id, cookies, settings.request_timeout_seconds
    )
    free_episodes = naver_api.free_episodes_only(all_episodes)

    # last_downloaded_no가 0(=아직 한 번도 추론 안 됨)인데 폴더에 파일이 이미 있으면
    # (예: 이전 시스템에서 넘어온 웹툰), 폴더에 있는 회차 수를 기준으로 몇 화까지
    # 받았는지 한 번 추론해서 기록한다. 그래야 전체를 다시 받지 않는다.
    last_no = webtoon.last_downloaded_no
    if last_no == 0:
        existing_count = count_existing_episode_entries(webtoon_dir)
        if existing_count > 0:
            inferred_index = min(existing_count, len(free_episodes)) - 1
            if inferred_index >= 0:
                last_no = free_episodes[inferred_index].episode_no
                repository.update_last_downloaded_no(title_id, last_no)
                job_status.log_line(
                    "download",
                    f"[{info.title_name}] 기존 폴더에서 {existing_count}개 회차 발견 → {last_no}화까지 완료로 표시",
                )

    if comicinfo.needs_comicinfo(webtoon_dir):
        webtoon_dir.mkdir(parents=True, exist_ok=True)
        comicinfo.write_comicinfo_file(webtoon_dir, info)
        await comicinfo.download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
        job_status.log_line("download", f"[{info.title_name}] ComicInfo.xml / 커버 이미지 생성")

    new_episodes = [ep for ep in free_episodes if ep.episode_no > last_no]
    if not new_episodes:
        return

    job_status.log_line("download", f"[{info.title_name}] 새 회차 {len(new_episodes)}개 다운로드 시작")

    for episode in new_episodes:
        success, _episode_dir = await download_single_episode(
            session=session,
            title_id=title_id,
            title_name=info.title_name,
            webtoon_type=info.webtoon_type,
            episode=episode,
            cookies=cookies,
            download_root=settings.download_root,
            folder_zero_fill=settings.folder_zero_fill,
            image_zero_fill=settings.image_zero_fill,
            max_concurrent_downloads=settings.max_concurrent_downloads,
            timeout_seconds=settings.request_timeout_seconds,
        )

        if not success:
            job_status.log_line(
                "download", f"[{info.title_name}] {episode.episode_no}화 다운로드 실패 — 다음 실행에서 재시도"
            )
            break

        # 다운로드 → 압축 → 폴더 삭제 → (다음 루프에서) 다음 화, 순서로 진행한다.
        zip_episode_folders(webtoon_dir)
        repository.update_last_downloaded_no(title_id, episode.episode_no)
        job_status.log_line("download", f"[{info.title_name}] {episode.episode_no}화 완료 (압축 후 폴더 삭제)")
        await asyncio.sleep(settings.delay_seconds)


async def run_download_job() -> None:
    settings = get_settings()
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE)

    job_status.start("download")
    job_status.log_line("download", f"다운로드 스캔 시작 — 구독 중인 웹툰 {len(active_webtoons)}개")
    had_error = False

    async with aiohttp.ClientSession() as session:
        for webtoon in active_webtoons:
            try:
                await _download_new_episodes_for_one(session, settings, webtoon.title_id)
            except Exception as e:
                had_error = True
                log.error("웹툰(titleId=%s) 다운로드 처리 중 예외 — 다음 웹툰으로 진행: %s", webtoon.title_id, e)
                job_status.log_line("download", f"[{webtoon.title}] 처리 중 오류: {e}")

    job_status.log_line("download", "다운로드 스캔 종료")
    job_status.finish("download", success=not had_error)


async def run_discovery_job() -> None:
    settings = get_settings()
    job_status.start("discovery")
    had_error = False

    async with aiohttp.ClientSession() as session:
        try:
            job_status.log_line("discovery", "작가 기반 신작 스캔 시작")
            await tracker.scan_subscriptions_for_updates(session, settings)
            job_status.log_line("discovery", "작가 기반 신작 스캔 완료")
        except Exception as e:
            had_error = True
            log.error("작가 기반 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"작가 기반 신작 스캔 오류: {e}")

        try:
            job_status.log_line("discovery", "태그 기반 신작 스캔 시작")
            await tracker.scan_curation_tags(session, settings)
            job_status.log_line("discovery", "태그 기반 신작 스캔 완료")
        except Exception as e:
            had_error = True
            log.error("태그 기반 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"태그 기반 신작 스캔 오류: {e}")

    job_status.finish("discovery", success=not had_error)


async def run_commands_job() -> None:
    settings = get_settings()
    job_status.start("commands")
    had_error = False
    async with aiohttp.ClientSession() as session:
        try:
            await sync_completion_thread(session, settings)
            job_status.log_line("commands", "디스코드 명령 확인 완료")
        except Exception as e:
            had_error = True
            log.error("디스코드 명령 처리 중 예외: %s", e)
            job_status.log_line("commands", f"오류: {e}")
    job_status.finish("commands", success=not had_error)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_discovery_job,
        "interval",
        minutes=get_effective_interval("scan_interval_minutes"),
        id="discovery_job",
    )
    scheduler.add_job(
        run_download_job,
        "interval",
        minutes=get_effective_interval("download_interval_minutes"),
        id="download_job",
    )
    scheduler.add_job(
        run_commands_job,
        "interval",
        minutes=get_effective_interval("commands_only_interval_minutes"),
        id="commands_job",
    )
    return scheduler


def reschedule_all(scheduler: AsyncIOScheduler) -> None:
    scheduler.reschedule_job("discovery_job", trigger="interval", minutes=get_effective_interval("scan_interval_minutes"))
    scheduler.reschedule_job("download_job", trigger="interval", minutes=get_effective_interval("download_interval_minutes"))
    scheduler.reschedule_job(
        "commands_job", trigger="interval", minutes=get_effective_interval("commands_only_interval_minutes")
    )
