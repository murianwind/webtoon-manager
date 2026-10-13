"""신작 스캔 작업 — 관심 작가/태그의 새 작품과 카카오페이지 목록 갱신, 새로 완결된 작품 알림.
"""

import asyncio
import logging

import aiohttp

from app import discord_bot, job_status, kakao_catalog, new_episode_watch, repository, tracker
from app.config import get_settings

from app.scheduler.kakao_download import (
    _notify_kakao_newly_finished,
)

log = logging.getLogger(__name__)

_discovery_job_lock = asyncio.Lock()


async def _notify_newly_finished() -> None:
    """완결 감지됐는데 아직 디스코드로 알리지 않은 웹툰에 실시간 봇으로 확인 메시지를 보낸다."""
    await _notify_kakao_newly_finished()  # 봇이 꺼져 있어서 못 보낸 카카오 알림도 이때 다시 시도한다
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
            excluded = await tracker.exclude_auto_added_daily_plus(session, settings)
            if excluded:
                job_status.log_line("discovery", f"자동 추가된 매일+ 작품 {len(excluded)}개를 제외됨으로 정리했습니다: " + ", ".join(title for _, title in excluded))
        except Exception as e:
            had_error = True
            log.error("매일+ 작품 정리 중 예외: %s", e)
            job_status.log_line("discovery", f"매일+ 작품 정리 오류: {e}")

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
            job_status.log_line("discovery", "미구독 작가 신작 스캔 시작")
            added = await tracker.discover_titles_for_unlinked_watched_authors(session, settings)
            job_status.log_line("discovery", f"미구독 작가 신작 스캔 완료 ({len(added)}건 추가)")
        except Exception as e:
            had_error = True
            log.error("미구독 작가 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"미구독 작가 신작 스캔 오류: {e}")

        try:
            job_status.log_line("discovery", "태그 기반 신작 스캔 시작")
            await tracker.scan_curation_tags(session, settings)
            job_status.log_line("discovery", "태그 기반 신작 스캔 완료")
        except Exception as e:
            had_error = True
            log.error("태그 기반 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"태그 기반 신작 스캔 오류: {e}")

        try:
            job_status.log_line("discovery", "구독해제/제외됨 정보 갱신 시작")
            refreshed = await tracker.refresh_inactive_metadata(session, settings)
            job_status.log_line("discovery", f"구독해제/제외됨 {refreshed}개 정보 갱신 완료")
        except Exception as e:
            had_error = True
            log.error("구독해제/제외됨 정보 갱신 중 예외: %s", e)
            job_status.log_line("discovery", f"구독해제/제외됨 정보 갱신 오류: {e}")

        try:
            job_status.log_line("discovery", "구독해제 웹툰 새 에피소드 확인 시작")
            notified = await new_episode_watch.run(session, settings)
            job_status.log_line("discovery", f"구독해제 웹툰 새 에피소드 {notified}건 알림")
        except Exception as e:
            had_error = True
            log.error("구독해제 웹툰 새 에피소드 확인 중 예외: %s", e)
            job_status.log_line("discovery", f"구독해제 웹툰 새 에피소드 확인 오류: {e}")

        try:
            job_status.log_line("discovery", "카카오웹툰 작가 신작 스캔 시작")
            kakao_new_count = await tracker.scan_kakao_authors_for_new_titles(session, settings)
            job_status.log_line("discovery", f"카카오웹툰 신작 {kakao_new_count}건 자동 추가")
        except Exception as e:
            had_error = True
            log.error("카카오웹툰 신작 스캔 중 예외: %s", e)
            job_status.log_line("discovery", f"카카오웹툰 신작 스캔 오류: {e}")

    try:
        await _notify_newly_finished()
    except Exception as e:
        had_error = True
        log.error("완결 알림 처리 중 예외: %s", e)

    job_status.finish("discovery", success=not had_error)


async def run_kakao_catalog_refresh_job() -> None:
    """카카오페이지 전체목록 캐시를 3시간마다 백그라운드로 새로 채운다(카카오 기능이 켜져 있을 때만)."""
    if repository.get_setting("kakao_webtoons_enabled") != "1":
        return
    try:
        await kakao_catalog.refresh()
    except Exception as e:
        log.error("카카오 목록 정기 새로고침 중 예외: %s", e)
