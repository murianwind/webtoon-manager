"""네이버 자동 다운로드 — 받을 회차 정하기(plan), 배치로 받기, 작품 하나 처리.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from app import comicinfo, cookie_health, download_pacing, download_roots, job_status, naver_api, repository
from app.config import Settings
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str
from app.folder_scanner import find_last_downloaded_episode_no
from app.zipper import zip_episode_folders

@dataclass
class _NaverDownloadPlan:
    """한 작품의 이번 다운로드 계획 — 받을 회차(pending)와 그걸 받는 데 필요한 정보."""

    info: object
    cookies: dict
    webtoon_dir: Path
    pending: list


async def _plan_naver_download(
    session: aiohttp.ClientSession, settings: Settings, webtoon, adult_tracker: cookie_health.AdultFetchTracker
) -> "_NaverDownloadPlan | None":
    """정보/회차 목록을 확인하고, 폴더 기준으로 마지막 회차를 바로잡고, 정보 파일/커버를 갱신한 뒤 받을 회차를 계산한다. 이번에 할 일이 없는
    경우(정보 조회 실패, 성인 쿠키 없음)는 None. 받을 회차가 없어도 계획은 돌려준다(pending이 빈 목록)."""
    title_id = webtoon.title_id
    info = await naver_api.fetch_title_info(session, title_id, settings.request_timeout_seconds)
    if info is None:
        job_status.log_line("download", f"[{webtoon.title}] 정보 조회 실패, 건너뜀")
        return None

    repository.update_is_adult(title_id, info.is_adult)

    cookies = get_adult_cookies(settings.cookie_file_path) if info.is_adult else {}
    if info.is_adult and not cookies:
        job_status.log_line("download", f"[{info.title_name}] 성인 웹툰 인증 쿠키 없음, 건너뜀")
        return None
    cookies = cookies or {}

    webtoon_dir = Path(download_roots.naver_root(settings)) / remove_forbidden_str(info.title_name)
    try:
        all_episodes = await naver_api.fetch_all_episodes(session, title_id, cookies, settings.request_timeout_seconds)
    except naver_api.NaverApiError as e:
        # 뒤쪽 페이지를 못 받은 불완전한 목록으로 진행하면 빠진 회차가 영영 누락될 수 있다 — 이 작품만 이번엔 건너뛰고 다음 실행에서 다시 시도한다.
        job_status.log_line("download", f"[{info.title_name}] 회차 목록 조회 실패, 건너뜀 — {e}")
        return None
    if info.is_adult:
        # 쿠키 만료 감지용 — 별도 API 호출 없이, 이번에 실제로 받아온 회차 개수를 그대로 신호로 쓴다.
        adult_tracker.record(len(all_episodes))

    free_episodes = naver_api.free_episodes_only(all_episodes)
    if free_episodes:
        repository.update_latest_episode_no(title_id, free_episodes[-1].episode_no)

    # DB에 저장된 last_downloaded_no만 믿지 않고, 매번 실제 폴더의 마지막 zip 파일명을 부제목 기준으로 네이버 회차 목록과 대조해서
    # 확인한다. 네이버 회차 번호(no)는 가끔 건너뛰기 때문에(예: 109 다음이 111), 로컬 zip 개수를 세서 위치로 추론하면 어긋난다 — 그래서
    # 반드시 부제목 텍스트로 실제 회차를 찾아야 한다.
    last_no = webtoon.last_downloaded_no
    folder_last_no = find_last_downloaded_episode_no(webtoon_dir, free_episodes)
    if folder_last_no > last_no:
        job_status.log_line(
            "download", f"[{info.title_name}] 폴더 확인 결과 {folder_last_no}화까지 완료 (DB 기록 {last_no}화에서 갱신)"
        )
        last_no = folder_last_no
        repository.update_last_downloaded_no(title_id, last_no)

    webtoon_dir.mkdir(parents=True, exist_ok=True)
    comicinfo.write_comicinfo_file(webtoon_dir, info)
    if comicinfo.needs_comicinfo(webtoon_dir):  # 지금은 커버 유무만 확인 — 커버는 없을 때만 받음
        await comicinfo.download_cover_image(session, webtoon_dir, info, settings.request_timeout_seconds)
    job_status.log_line("download", f"[{info.title_name}] ComicInfo.xml 갱신 / 커버 이미지 확인")

    return _NaverDownloadPlan(info, cookies, webtoon_dir, [ep for ep in free_episodes if ep.episode_no > last_no])


async def _download_pending_batches(
    session: aiohttp.ClientSession, settings: Settings, title_id: str, plan: _NaverDownloadPlan, failures: list[dict]
) -> None:
    """받을 회차를 번호순으로 받는다(다운로드 → 압축 → 폴더 삭제 → 다음 화). 상한에 걸리면 "다음 정기 실행까지 대기"가 아니라, 이번 실행
    안에서 batch_rest_minutes만큼 쉬었다가 이어서 계속 받는다 — 하루 한 번처럼 뜸하게 도는 스케줄에서는 다음 정기 실행까지 기다리면
    너무 오래 걸리기 때문. 하나라도 실패하면 그 즉시 이 작품은 전부 멈추고(실패 이력/알림 목록에 기록) 다음 실행에서 재시도한다."""
    info, pending = plan.info, list(plan.pending)
    total_pending = len(pending)
    job_status.log_line("download", f"[{info.title_name}] 새 회차 {total_pending}개 다운로드 시작")

    while pending:
        cap, rest_minutes = download_pacing.limits(settings)  # 설정 화면에서 바꾼 값이 있으면 그 값(배치마다 다시 읽어서 재시작 없이 적용)
        batch = pending[:cap] if cap > 0 else pending
        pending = pending[len(batch):]

        for episode in batch:
            success, _episode_dir = await download_single_episode(
                session=session,
                title_id=title_id,
                title_name=info.title_name,
                webtoon_type=info.webtoon_type,
                episode=episode,
                cookies=plan.cookies,
                download_root=download_roots.naver_root(settings),
                folder_zero_fill=settings.folder_zero_fill,
                image_zero_fill=settings.image_zero_fill,
                max_concurrent_downloads=settings.max_concurrent_downloads,
                timeout_seconds=settings.request_timeout_seconds,
            )
            if not success:
                job_status.log_line("download", f"[{info.title_name}] {episode.episode_no}화 다운로드 실패 — 다음 실행에서 재시도")
                repository.add_episode_history(
                    title_id, info.title_name, episode.episode_no, episode.subtitle, "failed", "이미지 URL 수집 또는 다운로드 오류"
                )
                failures.append({"title_name": info.title_name, "episode_no": episode.episode_no, "subtitle": episode.subtitle})
                return  # 이 작품은 여기서 완전히 중단 — 배치 남았어도 더 안 받음

            zip_episode_folders(plan.webtoon_dir)
            repository.update_last_downloaded_no(title_id, episode.episode_no)
            repository.add_episode_history(title_id, info.title_name, episode.episode_no, episode.subtitle, "success")
            job_status.log_line("download", f"[{info.title_name}] {episode.episode_no}화 완료 (압축 후 폴더 삭제)")
            await asyncio.sleep(settings.delay_seconds)

        if pending:
            job_status.log_line("download", f"[{info.title_name}] {cap}화 받음, 남은 {len(pending)}화는 {rest_minutes:g}분 쉬었다가 이어받기")
            await asyncio.sleep(rest_minutes * 60)


async def _download_new_episodes_for_one(
    session: aiohttp.ClientSession,
    settings: Settings,
    title_id: str,
    adult_tracker: cookie_health.AdultFetchTracker,
    failures: list[dict],
) -> None:
    webtoon = repository.get(title_id)
    if webtoon is None or webtoon.status != repository.STATUS_ACTIVE:
        return
    plan = await _plan_naver_download(session, settings, webtoon, adult_tracker)
    if plan is None or not plan.pending:
        return
    await _download_pending_batches(session, settings, title_id, plan, failures)
