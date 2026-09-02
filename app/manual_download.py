"""
수동 다운로드: titleId 하나를 분석해서 회차별 보유 여부를 보여주고, 사용자가 고른
회차만(또는 전체) 그 자리에서 내려받는다.

자동 다운로드(scheduler.download_job)와 달리 이어받기 연속성을 강제하지 않는다 —
사용자가 중간 회차 몇 개만 콕 집어 받을 수도 있다. 다만 다운로드가 끝난 뒤
find_last_downloaded_episode_no로 다시 확인해서, 이 titleId가 구독 목록에 있다면
last_downloaded_no를 자연스럽게 갱신한다(있을 때만; 구독 안 한 임의 작품이면 건너뜀).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from app import job_status, naver_api, repository
from app.comicinfo import download_cover_image, needs_comicinfo, write_comicinfo_file
from app.config import Settings
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import find_last_downloaded_episode_no
from app.models import TitleInfo
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)

JOB_NAME = "manual"


@dataclass
class ManualEpisodeRow:
    episode_no: int
    subtitle: str
    owned: bool
    is_locked: bool


async def analyze(title_id: str, settings: Settings) -> tuple[TitleInfo | None, list[ManualEpisodeRow]]:
    async with aiohttp.ClientSession() as session:
        info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
        if info is None:
            return None, []

        cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
        all_episodes = await naver_api.fetch_all_episodes(
            session, title_id, cookies or {}, settings.request_timeout_seconds
        )

    free_episodes = naver_api.free_episodes_only(all_episodes)
    safe_title = remove_forbidden_str(info.title_name)
    webtoon_dir = Path(settings.download_root) / safe_title
    owned_up_to = find_last_downloaded_episode_no(webtoon_dir, free_episodes)

    rows = [
        ManualEpisodeRow(
            episode_no=ep.episode_no,
            subtitle=ep.subtitle,
            owned=ep.episode_no <= owned_up_to,
            is_locked=ep.is_locked,
        )
        for ep in all_episodes
    ]
    return info, rows


async def download_selected(title_id: str, episode_nos: list[int], settings: Settings) -> None:
    job_status.start(JOB_NAME)
    job_status.log_line(JOB_NAME, f"titleId={title_id} 분석 중...")

    async with aiohttp.ClientSession() as session:
        info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
        if info is None:
            job_status.log_line(JOB_NAME, "웹툰 정보를 가져오지 못했습니다 (titleId 확인 필요)")
            job_status.finish(JOB_NAME, success=False)
            return

        cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
        if info.is_adult and not cookies:
            job_status.log_line(JOB_NAME, f"[{info.title_name}] 성인 웹툰 인증 쿠키가 없습니다")
            job_status.finish(JOB_NAME, success=False)
            return
        cookies = cookies or {}

        all_episodes = await naver_api.fetch_all_episodes(
            session, title_id, cookies, settings.request_timeout_seconds
        )
        wanted_nos = set(episode_nos)
        target_episodes = [ep for ep in all_episodes if ep.episode_no in wanted_nos]
        target_episodes.sort(key=lambda ep: ep.episode_no)

        if not target_episodes:
            job_status.log_line(JOB_NAME, "선택한 회차를 목록에서 찾지 못했습니다")
            job_status.finish(JOB_NAME, success=False)
            return

        safe_title = remove_forbidden_str(info.title_name)
        webtoon_dir = Path(settings.download_root) / safe_title

        if needs_comicinfo(webtoon_dir):
            webtoon_dir.mkdir(parents=True, exist_ok=True)
            write_comicinfo_file(webtoon_dir, info)
            await download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
            job_status.log_line(JOB_NAME, f"[{info.title_name}] info.xml / 커버 이미지 생성")

        had_failure = False
        job_status.log_line(JOB_NAME, f"[{info.title_name}] {len(target_episodes)}개 회차 다운로드 시작")

        for episode in target_episodes:
            if episode.is_locked:
                job_status.log_line(JOB_NAME, f"{episode.episode_no}화: 유료/잠김 — 건너뜀")
                continue

            success, _dir = await download_single_episode(
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
                had_failure = True
                job_status.log_line(
                    JOB_NAME, f"❌ {episode.episode_no}화 \"{episode.subtitle}\" 다운로드 실패 (이미지 URL 수집 또는 다운로드 오류)"
                )
                repository.add_episode_history(
                    title_id, info.title_name, episode.episode_no, episode.subtitle, "failed", "이미지 URL 수집 또는 다운로드 오류"
                )
                continue

            zip_episode_folders(webtoon_dir)
            repository.add_episode_history(title_id, info.title_name, episode.episode_no, episode.subtitle, "success")
            job_status.log_line(JOB_NAME, f"✅ {episode.episode_no}화 \"{episode.subtitle}\" 완료 (압축 후 폴더 삭제)")

        # 이 titleId가 구독 목록에 있으면 last_downloaded_no를 실제 폴더 상태 기준으로 갱신
        if repository.exists(title_id):
            free_episodes = naver_api.free_episodes_only(all_episodes)
            new_last_no = find_last_downloaded_episode_no(webtoon_dir, free_episodes)
            existing = repository.get(title_id)
            if existing and new_last_no > existing.last_downloaded_no:
                repository.update_last_downloaded_no(title_id, new_last_no)

        job_status.log_line(JOB_NAME, "수동 다운로드 종료")
        job_status.finish(JOB_NAME, success=not had_failure)
