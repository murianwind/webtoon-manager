"""
회차 이미지 다운로드 엔진.

기존 NWebtoon_Downloader의 module/webtoon/downloader.py를 이식하되, pywinauto
GUI 자동화 계층은 제거했다. 회차는 한 번에 하나씩만 처리한다(download_single_episode) —
호출자(scheduler.py)가 "다운로드 → 압축 → 폴더 삭제 → 다음 화"를 회차마다 반복하기
때문에, 이 파일은 회차 하나를 완전히 받는 책임만 진다 (여러 회차를 동시에 받지 않음).
회차 내부의 이미지 여러 장은 max_concurrent_downloads로 동시에 받는다.

폴더/파일 네이밍은 기존 규칙과 동일하게 유지한다:
  {download_root}/{title}/[{episode_no:04d}] {subtitle}/{image_no:04d}{ext}
"""

import asyncio
import logging
import random
import shutil
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp
from bs4 import BeautifulSoup

from app.constants import (
    DEFAULT_HEADERS,
    IMAGE_DOWNLOAD_MAX_RETRIES,
    MIN_VALID_IMAGE_BYTES,
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


async def _fetch_episode_image_urls(
    session: aiohttp.ClientSession,
    detail_url: str,
    title_id: str,
    episode: EpisodeInfo,
    cookies: dict[str, str],
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
                cookies=cookies,
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
    session: aiohttp.ClientSession,
    img_url: str,
    file_path: Path,
    cookies: dict[str, str],
    timeout_seconds: int,
) -> bool:
    for attempt in range(IMAGE_DOWNLOAD_MAX_RETRIES + 1):
        try:
            async with session.get(
                img_url,
                headers=DEFAULT_HEADERS,
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status == 200:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

                    # 상태코드가 200이어도, 네이버가 에러 페이지나 빈 응답을 200으로
                    # 줄 수 있다 — 실제로 예전 webtoon_checker.py가 "10KB 이하 파일"을
                    # 별도로 사후 탐지하던 이유가 이것이다. 크기가 너무 작으면 여기서
                    # 바로 실패로 처리해서 재시도 루프를 타게 한다(사후 탐지보다 안전).
                    actual_size = file_path.stat().st_size
                    if actual_size < MIN_VALID_IMAGE_BYTES:
                        log.warning(
                            "이미지 크기 이상(%d bytes < %d) — 재시도: %s",
                            actual_size, MIN_VALID_IMAGE_BYTES, img_url,
                        )
                        file_path.unlink(missing_ok=True)
                    else:
                        return True
        except Exception as e:
            if attempt >= IMAGE_DOWNLOAD_MAX_RETRIES:
                log.error("이미지 다운로드 최종 실패: %s (%s)", img_url, e)
                return False

        if attempt < IMAGE_DOWNLOAD_MAX_RETRIES:
            delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt) * (1 + random.uniform(0, 0.2))
            await asyncio.sleep(delay)

    return False


async def download_single_episode(
    session: aiohttp.ClientSession,
    title_id: str,
    title_name: str,
    webtoon_type: str,
    episode: EpisodeInfo,
    cookies: dict[str, str],
    download_root: str,
    folder_zero_fill: int,
    image_zero_fill: int,
    max_concurrent_downloads: int,
    timeout_seconds: int,
) -> tuple[bool, Optional[Path]]:
    """
    회차 하나를 완전히 받는다. 이미지 목록을 못 가져오거나 일부라도 다운로드에
    실패하면 (False, episode_dir)를 반환한다 — 호출자가 이 경우 다음 회차로
    넘어가지 않고 멈춰야, "다음 실행 때 이 화부터 재시도"가 성립한다.
    """
    detail_url = NAVER_DETAIL_URL_TEMPLATES.get(webtoon_type, NAVER_DETAIL_URL_TEMPLATES["webtoon"])
    safe_title = remove_forbidden_str(title_name)

    img_urls = await _fetch_episode_image_urls(
        session, detail_url, title_id, episode, cookies, timeout_seconds
    )
    if not img_urls:
        return False, None

    episode_dir = (
        Path(download_root)
        / safe_title
        / episode_folder_name(episode.episode_no, episode.subtitle, folder_zero_fill)
    )
    episode_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent_downloads)

    async def _bounded(img_url: str, file_path: Path) -> bool:
        async with semaphore:
            return await _download_single_image(session, img_url, file_path, cookies, timeout_seconds)

    tasks = []
    for image_index, img_url in enumerate(img_urls):
        ext = guess_image_extension(img_url)
        file_name = image_file_name(image_index + 1, image_zero_fill, ext)
        tasks.append(_bounded(img_url, episode_dir / file_name))

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for o in outcomes if o is True) == len(img_urls)
    if not success:
        # 일부만 받은 채로 폴더가 남으면, 다음 재시도 때 그 위에 다시 받으면서
        # 어떤 이미지가 실제로 실패했었는지 구분이 안 되고 자리만 차지한다 —
        # 실패하면 통째로 지워서 다음 실행이 깨끗한 상태에서 다시 받게 한다.
        shutil.rmtree(episode_dir, ignore_errors=True)
        return False, None
    return True, episode_dir
