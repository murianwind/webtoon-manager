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

from app import download_roots, job_status, naver_api, repository
from app.comicinfo import build_comicinfo_xml, download_cover_image, fetch_cover_image, needs_comicinfo, write_comicinfo_file
from app.config import Settings
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import find_last_downloaded_episode_no
from app.metadata_zip import COVER_MISSING, MetadataBundle
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
    recorded: bool = False  # 폴더에는 없고 "받은 회차 기록"(여기까지 받음)으로만 받은 것으로 보이는 회차


class ManualRecordError(Exception):
    """받은 기록을 고칠 수 없을 때(사용자에게 보일 이유 포함)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def analyze(title_id: str, settings: Settings, recorded_up_to: int = 0) -> tuple[TitleInfo | None, list[ManualEpisodeRow]]:
    """recorded_up_to: 구독 중인 작품의 "여기까지 받음" 기록(last_downloaded_no) — 폴더에 없어도 그 번호까지는 받은 것으로 보이게 한다(recorded로 구분). 0이면 폴더 기준만."""
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
    webtoon_dir = Path(download_roots.naver_root(settings)) / safe_title
    owned_up_to = find_last_downloaded_episode_no(webtoon_dir, free_episodes)

    rows = [
        ManualEpisodeRow(
            episode_no=ep.episode_no,
            subtitle=ep.subtitle,
            owned=ep.episode_no <= max(owned_up_to, recorded_up_to),
            is_locked=ep.is_locked,
            recorded=owned_up_to < ep.episode_no <= recorded_up_to,
        )
        for ep in all_episodes
    ]
    return info, rows


async def mark_record(title_id: str, episode_nos: list[int], downloaded: bool, settings: Settings) -> dict:
    """수동 다운로드에서 고른 회차를 "받은 것으로" 또는 "받지 않은 것으로" 기록한다 — 구독 중인 작품만. 네이버는 회차마다가 아니라 "여기까지 받음"(last_downloaded_no)
    한 지점만 기억하므로, 받은 것으로는 고른 가장 큰 번호까지(이미 더 뒤면 그대로), 받지 않은 것으로는 고른 가장 작은 번호부터 모두 받을 대상이 되도록 그 앞 번호로
    되돌린다. 잠긴(유료/미공개) 회차는 기록하지 않는다. 폴더에 그보다 뒤까지 파일이 있으면 다음 실행에서 폴더 기준으로 다시 올라가므로 경고를 돌려준다."""
    tracked = repository.get(title_id)
    if tracked is None or tracked.status != repository.STATUS_ACTIVE:
        raise ManualRecordError("구독 중인 웹툰만 받은 기록을 바꿀 수 있습니다. 먼저 구독해주세요.")
    info, rows = await analyze(title_id, settings)  # 폴더 기준 값(owned)만 필요해서 기록은 합치지 않는다
    if info is None:
        raise ManualRecordError("작품 정보를 가져오지 못했습니다. 잠시 뒤 다시 시도해주세요.", 502)
    by_no = {row.episode_no: row for row in rows}
    selected = sorted({n for n in episode_nos if n in by_no})
    ignored_locked = [n for n in selected if by_no[n].is_locked]
    targets = [n for n in selected if not by_no[n].is_locked]
    if not targets:
        raise ManualRecordError("기록할 수 있는 회차가 없습니다(유료/잠긴 회차는 기록할 수 없습니다).")
    previous = tracked.last_downloaded_no
    folder_last = max((row.episode_no for row in rows if row.owned), default=0)
    new_last = max(previous, targets[-1]) if downloaded else max(0, min(previous, targets[0] - 1))
    repository.update_last_downloaded_no(title_id, new_last)
    warning = None
    if not downloaded and folder_last > new_last:
        warning = f"폴더에 {folder_last}화까지 파일이 있어서 다음 실행에서 다시 {folder_last}화까지 받은 것으로 인식됩니다(파일을 지우면 반영됩니다)."
    return {"previous": previous, "last_downloaded_no": new_last, "marked": targets, "ignored_locked": ignored_locked, "folder_last_no": folder_last, "warning": warning}


async def build_metadata_bundle(title_id: str, settings: Settings) -> MetadataBundle | None:
    """분석한 웹툰의 info.xml과 표지를 서버 폴더에 쓰지 않고 메모리로 만든다(브라우저 다운로드용 "표지·정보 받기"). 작품 정보를 못 받으면 None이고,
    표지만 못 받으면 info.xml만 담고 warnings에 표시를 남긴다."""
    async with aiohttp.ClientSession() as session:
        info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
        if info is None:
            return None
        cover = await fetch_cover_image(session, info, settings.request_timeout_seconds)
    files = {"info.xml": build_comicinfo_xml(info).encode("utf-8")}
    warnings: list[str] = []
    if cover is None:
        warnings.append(COVER_MISSING)
    else:
        extension, image = cover
        files[f"cover{extension}"] = image
    return MetadataBundle(title=info.title_name, files=files, warnings=warnings)


def _fail(message: str) -> None:
    """수동 다운로드를 오류로 끝낸다(사용자에게 보이는 로그 한 줄 + 상태)."""
    job_status.log_line(JOB_NAME, message)
    job_status.finish(JOB_NAME, success=False)


async def _refresh_metadata(session: aiohttp.ClientSession, settings: Settings, webtoon_dir: Path, info: TitleInfo) -> None:
    """작품 폴더를 만들고 info.xml을 갱신한다(커버는 없을 때만 받음 — 수동 다운로드 중에는 기존 동작 그대로)."""
    webtoon_dir.mkdir(parents=True, exist_ok=True)
    write_comicinfo_file(webtoon_dir, info)
    if needs_comicinfo(webtoon_dir):  # 지금은 커버 유무만 확인 — 커버는 없을 때만 받음
        await download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
    job_status.log_line(JOB_NAME, f"[{info.title_name}] info.xml 갱신 / 커버 이미지 확인")


async def _download_episode(
    session: aiohttp.ClientSession, settings: Settings, title_id: str, info: TitleInfo, cookies: dict, webtoon_dir: Path, episode
) -> bool:
    """회차 하나를 받아 압축하고 이력을 남긴다(잠긴 회차는 건너뜀 → True로 취급). 실패하면 False — 호출부는 실패해도 다음 회차를 계속 받는다."""
    if episode.is_locked:
        job_status.log_line(JOB_NAME, f"{episode.episode_no}화: 유료/잠김 — 건너뜀")
        return True
    success, _dir = await download_single_episode(
        session=session,
        title_id=title_id,
        title_name=info.title_name,
        webtoon_type=info.webtoon_type,
        episode=episode,
        cookies=cookies,
        download_root=download_roots.naver_root(settings),
        folder_zero_fill=settings.folder_zero_fill,
        image_zero_fill=settings.image_zero_fill,
        max_concurrent_downloads=settings.max_concurrent_downloads,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not success:
        job_status.log_line(JOB_NAME, f"❌ {episode.episode_no}화 \"{episode.subtitle}\" 다운로드 실패 (이미지 URL 수집 또는 다운로드 오류)")
        repository.add_episode_history(
            title_id, info.title_name, episode.episode_no, episode.subtitle, "failed", "이미지 URL 수집 또는 다운로드 오류"
        )
        return False
    zip_episode_folders(webtoon_dir)
    repository.add_episode_history(title_id, info.title_name, episode.episode_no, episode.subtitle, "success")
    job_status.log_line(JOB_NAME, f"✅ {episode.episode_no}화 \"{episode.subtitle}\" 완료 (압축 후 폴더 삭제)")
    return True


def _sync_last_downloaded_no(title_id: str, webtoon_dir: Path, all_episodes: list) -> None:
    """이 titleId가 구독 목록에 있으면 last_downloaded_no를 실제 폴더 상태 기준으로 갱신한다(앞설 때만 — 내리지 않는다)."""
    if not repository.exists(title_id):
        return
    new_last_no = find_last_downloaded_episode_no(webtoon_dir, naver_api.free_episodes_only(all_episodes))
    existing = repository.get(title_id)
    if existing and new_last_no > existing.last_downloaded_no:
        repository.update_last_downloaded_no(title_id, new_last_no)


async def download_selected(title_id: str, episode_nos: list[int], settings: Settings) -> None:
    job_status.start(JOB_NAME)
    job_status.log_line(JOB_NAME, f"titleId={title_id} 분석 중...")

    async with aiohttp.ClientSession() as session:
        info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
        if info is None:
            _fail("웹툰 정보를 가져오지 못했습니다 (titleId 확인 필요)")
            return

        cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
        if info.is_adult and not cookies:
            _fail(f"[{info.title_name}] 성인 웹툰 인증 쿠키가 없습니다")
            return
        cookies = cookies or {}

        all_episodes = await naver_api.fetch_all_episodes(session, title_id, cookies, settings.request_timeout_seconds)
        wanted_nos = set(episode_nos)
        target_episodes = sorted((ep for ep in all_episodes if ep.episode_no in wanted_nos), key=lambda ep: ep.episode_no)
        if not target_episodes:
            _fail("선택한 회차를 목록에서 찾지 못했습니다")
            return

        webtoon_dir = Path(download_roots.naver_root(settings)) / remove_forbidden_str(info.title_name)
        await _refresh_metadata(session, settings, webtoon_dir, info)

        job_status.log_line(JOB_NAME, f"[{info.title_name}] {len(target_episodes)}개 회차 다운로드 시작")
        had_failure = False
        for episode in target_episodes:
            if not await _download_episode(session, settings, title_id, info, cookies, webtoon_dir, episode):
                had_failure = True  # 실패해도 나머지 회차는 계속 받는다(직접 고른 것이라 순서를 끊을 이유가 없다)

        _sync_last_downloaded_no(title_id, webtoon_dir, all_episodes)
        job_status.log_line(JOB_NAME, "수동 다운로드 종료")
        job_status.finish(JOB_NAME, success=not had_failure)
