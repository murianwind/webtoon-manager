"""
회차 이미지 다운로드 엔진.

기존 NWebtoon_Downloader의 module/webtoon/downloader.py를 그대로 이식하되,
콘솔 GUI를 pywinauto로 조작하던 download.py의 자동화 계층은 제거했다.
이 모듈은 함수 호출만으로 다운로드가 끝나므로 스케줄러가 직접 부를 수 있다.

폴더/파일 네이밍은 기존 규칙과 동일하게 유지한다:
  {download_root}/{title}/[{episode_no:04d}] {subtitle}/{image_no:04d}{ext}
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import aiohttp
from bs4 import BeautifulSoup

from app.constants import (
    DEFAULT_HEADERS,
    IMAGE_DOWNLOAD_MAX_RETRIES,
    NAVER_DETAIL_URL_TEMPLATES,
    RETRY_BACKOFF_BASE_SECONDS,
)
from app.file_utils import (
    episode_folder_name,
    guess_image_extension,
    image_file_name,
    remove_forbidden_str,
)
from app.models import EpisodeInfo

log = logging.getLogger(__name__)


@dataclass
class EpisodeDownloadResult:
    episode_no: int
    subtitle: str
    success: bool
    image_count: int


async def _fetch_episode_image_urls(
    session: aiohttp.ClientSession,
    detail_url: str,
    title_id: str,
    episode: EpisodeInfo,
    timeout_seconds: int,
) -> list[str]:
    """회차 상세 페이지 HTML을 받아 div.wt_viewer 안의 이미지 URL들을 추출한다."""
    params = {"titleId": title_id, "no": episode.episode_no}

    last_error: Exception | None = None
    for attempt in range(IMAGE_DOWNLOAD_MAX_RETRIES + 1):
        try:
            async with session.get(
                detail_url,
                params=params,
                headers=DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, "lxml")
                    viewer = soup.select_one("div.wt_viewer")
                    if not viewer:
                        return []
                    return [img["src"] for img in viewer.find_all("img") if img.get("src")]

                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else (
                        RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
                    )
                else:
                    delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
                last_error = Exception(f"HTTP {response.status}")
        except Exception as e:
            last_error = e
            delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt)

        if attempt < IMAGE_DOWNLOAD_MAX_RETRIES:
            await asyncio.sleep(delay)

    log.error(
        "%s화: 이미지 URL 수집 최종 실패 (titleId=%s): %s", episode.episode_no, title_id, last_error
    )
    return []


async def _download_single_image(
    session: aiohttp.ClientSession, img_url: str, file_path: Path, timeout_seconds: int
) -> bool:
    for attempt in range(IMAGE_DOWNLOAD_MAX_RETRIES + 1):
        try:
            async with session.get(
                img_url, headers=DEFAULT_HEADERS, timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as response:
                if response.status == 200:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                    return True
        except Exception as e:
            if attempt >= IMAGE_DOWNLOAD_MAX_RETRIES:
                log.error("이미지 다운로드 최종 실패: %s (%s)", img_url, e)
                return False

        if attempt < IMAGE_DOWNLOAD_MAX_RETRIES:
            delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt) * (1 + random.uniform(0, 0.2))
            await asyncio.sleep(delay)

    return False


async def download_webtoon_episodes(
    title_id: str,
    title_name: str,
    webtoon_type: str,
    episodes: list[EpisodeInfo],
    cookies: dict[str, str],
    download_root: str,
    folder_zero_fill: int,
    image_zero_fill: int,
    batch_size: int,
    max_concurrent_downloads: int,
    delay_seconds: float,
    timeout_seconds: int,
) -> list[EpisodeDownloadResult]:
    """
    주어진 회차 목록을 배치 단위로 이미지 URL을 수집한 뒤, 세마포어로 동시성을
    제한하며 전부 다운로드한다. 한 회차가 실패해도 나머지 회차 처리는 계속된다.
    """
    if not episodes:
        return []

    detail_url = NAVER_DETAIL_URL_TEMPLATES.get(webtoon_type, NAVER_DETAIL_URL_TEMPLATES["webtoon"])
    safe_title = remove_forbidden_str(title_name)
    semaphore = asyncio.Semaphore(max_concurrent_downloads)

    async with aiohttp.ClientSession(cookies=cookies) as session:
        results: list[EpisodeDownloadResult] = []

        for batch_start in range(0, len(episodes), batch_size):
            batch = episodes[batch_start : batch_start + batch_size]

            url_tasks = [
                _fetch_episode_image_urls(session, detail_url, title_id, ep, timeout_seconds)
                for ep in batch
            ]
            batch_img_urls = await asyncio.gather(*url_tasks)

            download_tasks = []
            task_episode_map: list[tuple[EpisodeInfo, int]] = []  # (episode, image_count)

            # 폴더는 회차 순서대로 먼저 만들어둔다 (동시 다운로드 중 생성 순서가 뒤섞이지
            # 않도록). change.py 쪽에서 폴더 생성 순서를 회차 순서로 신뢰하기 때문에 중요하다.
            episode_dirs_in_order: dict[int, Path] = {}
            for episode, img_urls in zip(batch, batch_img_urls):
                if not img_urls:
                    continue
                episode_dir = (
                    Path(download_root)
                    / safe_title
                    / episode_folder_name(episode.episode_no, episode.subtitle, folder_zero_fill)
                )
                episode_dir.mkdir(parents=True, exist_ok=True)
                episode_dirs_in_order[episode.episode_no] = episode_dir

            for episode, img_urls in zip(batch, batch_img_urls):
                if not img_urls:
                    results.append(
                        EpisodeDownloadResult(episode.episode_no, episode.subtitle, False, 0)
                    )
                    continue

                episode_dir = episode_dirs_in_order[episode.episode_no]
                task_episode_map.append((episode, len(img_urls)))

                async def _download_one_episode(session, episode_dir, img_urls, episode):
                    async def _bounded(img_url, file_path):
                        async with semaphore:
                            return await _download_single_image(session, img_url, file_path, timeout_seconds)

                    tasks = []
                    for image_index, img_url in enumerate(img_urls):
                        ext = guess_image_extension(img_url)
                        file_name = image_file_name(image_index + 1, image_zero_fill, ext)
                        tasks.append(_bounded(img_url, episode_dir / file_name))
                    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                    success_count = sum(1 for o in outcomes if o is True)
                    return success_count == len(img_urls)

                download_tasks.append(_download_one_episode(session, episode_dir, img_urls, episode))

            episode_success_flags = await asyncio.gather(*download_tasks) if download_tasks else []
            for (episode, image_count), success in zip(task_episode_map, episode_success_flags):
                results.append(
                    EpisodeDownloadResult(episode.episode_no, episode.subtitle, success, image_count)
                )

            if batch_start + batch_size < len(episodes):
                await asyncio.sleep(delay_seconds)

        return results
