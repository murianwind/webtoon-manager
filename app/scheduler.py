"""
주기 작업 정의 + 진행상황 로깅 + 잡별 스케줄(끄기/N분마다/특정 요일·시각) 관리.

두 가지 독립적인 작업으로 나눈다:
  - discovery_job   : 완결 감지(+감지 즉시 디스코드 봇으로 확인 메시지 전송) + 작가/태그 신작 자동추가
  - download_job    : 구독 중인 웹툰의 새 회차 다운로드 (회차 하나마다 압축까지 끝내고 다음 화로 진행)

(예전에는 디스코드 완결-확인 스레드를 폴링하는 commands_job이 따로 있었지만,
discord_bot.py의 실시간 Gateway 봇으로 대체되어 더 이상 필요 없다 — 폴링 자체가 없어짐.)

각 잡은 웹툰 하나 처리 중 예외가 나도 다른 웹툰 처리를 막지 않도록 individually try/except.
각 잡의 스케줄은 DB(settings 테이블)에 사용자가 저장한 값이 있으면 그걸 쓰고, 없으면
기본값(신작 스캔 6시간마다 / 다운로드 1시간마다, 전부 interval 모드)을 쓴다 —
설정 페이지에서 바꾸면 reschedule_all()로 즉시 반영된다.
"""

import asyncio
import logging
from pathlib import Path

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import comicinfo, discord_bot, job_status, naver_api, repository, schedule_config, tracker
from app.config import Settings, get_settings
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import find_last_downloaded_episode_no
from app.schedule_config import JobSchedule
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)

DEFAULT_SCHEDULES: dict[str, JobSchedule] = {
    "discovery_job": JobSchedule(mode="interval", interval_minutes=360),
    "download_job": JobSchedule(mode="interval", interval_minutes=60),
}

# 정기 스케줄 실행과 "수동 실행" 버튼이 겹치는 걸 막는 잡별 락 (동시성 문제 방지).
_download_job_lock = asyncio.Lock()
_discovery_job_lock = asyncio.Lock()


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

    if free_episodes:
        repository.update_latest_episode_no(title_id, free_episodes[-1].episode_no)

    # DB에 저장된 last_downloaded_no만 믿지 않고, 매번 실제 폴더의 마지막 zip 파일명을
    # 부제목 기준으로 네이버 회차 목록과 대조해서 확인한다. 네이버 회차 번호(no)는
    # 가끔 건너뛰기 때문에(예: 109 다음이 111), 로컬 zip 개수를 세서 위치로 추론하면
    # 어긋난다 — 그래서 반드시 부제목 텍스트로 실제 회차를 찾아야 한다.
    last_no = webtoon.last_downloaded_no
    folder_last_no = find_last_downloaded_episode_no(webtoon_dir, free_episodes)
    if folder_last_no > last_no:
        job_status.log_line(
            "download",
            f"[{info.title_name}] 폴더 확인 결과 {folder_last_no}화까지 완료 (DB 기록 {last_no}화에서 갱신)",
        )
        last_no = folder_last_no
        repository.update_last_downloaded_no(title_id, last_no)

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
    """정기 스케줄과 '수동 실행' 버튼이 동시에 이 잡을 실행하면, 같은 웹툰을 두 실행이
    동시에 다운로드하려고 시도할 수 있다(회차 저장/압축이 원자적이지 않음) — 잡별
    락으로 겹치는 실행은 조용히 건너뛴다(에러 아님, 그냥 "이미 실행 중"으로 로그만 남김)."""
    if _download_job_lock.locked():
        job_status.log_line("download", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _download_job_lock:
        await _run_download_job_impl()


async def _run_download_job_impl() -> None:
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


async def _notify_newly_finished() -> None:
    """완결 감지됐는데 아직 디스코드로 알리지 않은 웹툰에 실시간 봇으로 확인 메시지를 보낸다."""
    to_notify = [
        wt
        for wt in repository.list_by_status(repository.STATUS_ACTIVE)
        if wt.is_finished and not wt.finish_notified and not wt.finish_ack
    ]
    if not to_notify:
        return

    sent = 0
    for wt in to_notify:
        try:
            await discord_bot.send_completion_prompt(wt.title_id, wt.title)
            repository.set_finish_notified(wt.title_id)
            sent += 1
        except Exception as e:
            log.error("완결 알림 전송 중 예외 (titleId=%s): %s", wt.title_id, e)

    job_status.log_line("discovery", f"완결 확인 알림 {sent}건 전송")


async def run_discovery_job() -> None:
    """download_job과 동일한 이유로 잡별 락을 건다 — 겹치는 실행은 건너뛴다."""
    if _discovery_job_lock.locked():
        job_status.log_line("discovery", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _discovery_job_lock:
        await _run_discovery_job_impl()


async def _run_discovery_job_impl() -> None:
    settings = get_settings()
    job_status.start("discovery")
    had_error = False

    async with aiohttp.ClientSession() as session:
        try:
            job_status.log_line("discovery", "썸네일 없는 웹툰 채우는 중")
            filled = await tracker.backfill_missing_thumbnails(session, settings)
            job_status.log_line("discovery", f"썸네일 {filled}개 채움")
        except Exception as e:
            had_error = True
            log.error("썸네일 백필 중 예외: %s", e)
            job_status.log_line("discovery", f"썸네일 백필 오류: {e}")

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

    try:
        await _notify_newly_finished()
    except Exception as e:
        had_error = True
        log.error("완결 알림 처리 중 예외: %s", e)

    job_status.finish("discovery", success=not had_error)


_JOB_FUNCS = {
    "discovery_job": run_discovery_job,
    "download_job": run_download_job,
}


def _build_trigger(schedule: JobSchedule):
    """off면 None(=스케줄 없음), interval이면 N분마다, cron이면 지정한 시:분/요일에."""
    if schedule.mode == "off":
        return None
    if schedule.mode == "cron":
        day_of_week = ",".join(schedule.cron_days) if schedule.cron_days else "*"
        return CronTrigger(hour=schedule.cron_hour, minute=schedule.cron_minute, day_of_week=day_of_week)
    return IntervalTrigger(minutes=schedule.interval_minutes)


def _apply_job_schedule(scheduler: AsyncIOScheduler, job_id: str) -> None:
    schedule = schedule_config.get_schedule(job_id, DEFAULT_SCHEDULES[job_id])
    trigger = _build_trigger(schedule)
    existing = scheduler.get_job(job_id)

    if trigger is None:
        if existing:
            scheduler.remove_job(job_id)
        return

    if existing:
        scheduler.reschedule_job(job_id, trigger=trigger)
    else:
        scheduler.add_job(_JOB_FUNCS[job_id], trigger=trigger, id=job_id)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
    return scheduler


def reschedule_all(scheduler: AsyncIOScheduler) -> None:
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
