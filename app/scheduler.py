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
from dataclasses import dataclass
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import download_roots, archiver, comicinfo, cookie_health, discord_bot, discord_notify, job_status, kakao_api, kakao_catalog, kakao_page_auth, kakao_page_download, naver_api, report_seen, repository, schedule_config, tracker, webtoon_server_client
from app import rclone_updater
from app.config import Settings, get_settings
from app.constants import NAVER_DETAIL_URL_TEMPLATES
from app.cookie_loader import get_adult_cookies
from app.downloader import download_single_episode
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
from app.folder_scanner import find_last_downloaded_episode_no
from app.schedule_config import JobSchedule
from app.zipper import zip_episode_folders

log = logging.getLogger(__name__)

DEFAULT_SCHEDULES: dict[str, JobSchedule] = {
    "discovery_job": JobSchedule(mode="interval", interval_minutes=360),
    "download_job": JobSchedule(mode="interval", interval_minutes=60),
    "report_job": JobSchedule(mode="off"),  # 사용자가 원하는 시각으로 직접 설정해야 켜짐
    "archive_job": JobSchedule(mode="off"),
}

# 정기 스케줄 실행과 "수동 실행" 버튼이 겹치는 걸 막는 잡별 락 (동시성 문제 방지).
_download_job_lock = asyncio.Lock()
_discovery_job_lock = asyncio.Lock()
_report_job_lock = asyncio.Lock()
_archive_job_lock = asyncio.Lock()


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
    all_episodes = await naver_api.fetch_all_episodes(session, title_id, cookies, settings.request_timeout_seconds)
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

    cap = settings.max_new_episodes_per_title
    while pending:
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
            rest_minutes = settings.batch_rest_minutes
            job_status.log_line("download", f"[{info.title_name}] {cap}화 받음, 남은 {len(pending)}화는 {rest_minutes}분 쉬었다가 이어받기")
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


_DOWNLOAD_TARGET_PLATFORMS = {"naver": {"naver"}, "kakao": {"kakao"}, "both": {"naver", "kakao"}}
_download_claimed_platforms: set[str] = set()  # 지금 실행 중이거나 순서를 기다리는 실행이 맡은 플랫폼


async def run_download_job(target: str = "naver") -> None:
    """target: naver | kakao | both(스케줄에서 고른 대상). 정기 스케줄과 '수동 실행' 버튼이 동시에 같은 플랫폼을
    받으려 하면 같은 웹툰을 두 실행이 동시에 다운로드할 수 있다(회차 저장/압축이 원자적이지 않음) — 이미 실행 중이거나
    기다리는 실행이 같은 플랫폼을 맡고 있으면 조용히 건너뛴다(에러 아님, "이미 실행 중"으로 로그만 남김). 서로 다른
    플랫폼(예: 네이버 스케줄과 카카오 스케줄이 같은 시각)은 건너뛰지 않고 순서를 기다렸다가 실행한다."""
    wanted = set(_DOWNLOAD_TARGET_PLATFORMS.get(target, {"naver"}))
    platforms = wanted - _download_claimed_platforms
    if not platforms:
        job_status.log_line("download", "이미 실행 중이거나 대기 중이라 건너뜁니다 (중복 실행 방지)")
        return
    if platforms != wanted:
        job_status.log_line("download", "이미 실행 중이거나 대기 중인 대상은 건너뛰고 나머지만 실행합니다 (중복 실행 방지)")
    _download_claimed_platforms.update(platforms)
    try:
        async with _download_job_lock:
            await _run_download_job_impl(platforms)
    finally:
        _download_claimed_platforms.difference_update(platforms)


# 카카오페이지 자동 다운로드는 네이버와 같은 설정(작품당 상한 max_new_episodes_per_title, 배치 사이 쉬는 시간 batch_rest_minutes)을
# 쓴다. 상한 0 이하는 "제한 없음"이라 한 번에 전부 받는다(아래 값은 그 경우의 요청당 상한일 뿐이다).
_KAKAO_UNLIMITED_EPISODES = 100_000
# 비정상 응답(계속 "받을 게 남았다"고만 하는 경우)으로 한 작품에서 무한히 도는 걸 막는 안전장치 — 상한 10이면 2000회차까지.
_KAKAO_MAX_BATCHES_PER_TITLE = 200


def _kakao_remaining_free(result) -> int:
    """이번 배치 뒤에도 받을 수 있는 회차가 몇 개 남았는지(상한에 걸려 못 받은 것). 기다무로 연 회차는 "받을 회차"가 아니라 잠긴 회차였으므로
    받은 개수에서 뺀다. 오류/실패가 있으면 0 — 그 작품은 여기서 멈춘다."""
    if result.error is not None or result.failed is not None:
        return 0
    downloaded_free = len(result.downloaded) - (1 if result.ticket_used is not None else 0)
    return max(0, len(result.plan.to_download) - downloaded_free)


def _record_kakao_batch(webtoon: dict, result, failures: list[dict]) -> None:
    """한 배치의 결과를 이력/로그/실패 목록에 남긴다."""
    title, series_id = webtoon["title"], str(webtoon["title_id"])
    for number, subtitle in result.downloaded_items:
        repository.add_episode_history(series_id, result.title, number, subtitle, "success", platform="kakao")
    if result.error:
        job_status.log_line("download", f"[{title}] {result.error}")
    elif result.failed is not None:
        repository.add_episode_history(series_id, result.title, result.failed, "", "failed", "이미지 받기 실패", platform="kakao")
        failures.append({"title_name": result.title, "episode_no": result.failed, "subtitle": "이미지 받기 실패"})
    elif not result.downloaded and result.plan.marker is not None and result.plan.marker.warning:
        job_status.log_line("download", f"[{title}] ⚠ 폴더의 파일(표식)이 사이트 회차와 맞지 않아 번호대로 이어받았습니다")


async def _download_kakao_title(client, settings, webtoon: dict, root: str, failures: list[dict]):
    """작품 하나를 받는다. 상한(작품당 한 번에 받는 수)에 걸려 받을 회차가 남으면, 네이버와 같이 batch_rest_minutes만큼 쉬었다가 같은
    실행 안에서 이어서 받는다(다음 정기 실행까지 기다리지 않는다). 실패/오류가 나면 거기서 멈춘다. 마지막 배치의 결과를 돌려준다(예외로
    중단되면 None) — 완결 판정은 이 결과로 한다."""
    title, series_id = webtoon["title"], webtoon["title_id"]
    cap = settings.max_new_episodes_per_title
    max_episodes = cap if cap > 0 else _KAKAO_UNLIMITED_EPISODES
    result = None
    for batch in range(1, _KAKAO_MAX_BATCHES_PER_TITLE + 1):
        try:
            result = await kakao_page_download.run_download(
                client, series_id=series_id, title=None, download_root=root, max_episodes=max_episodes,
                on_progress=lambda line, t=title: job_status.log_line("download", f"[{t}] {line}"),
            )
        except Exception as e:
            log.error("카카오페이지 웹툰(series_id=%s) 다운로드 중 예외 — 다음으로 진행: %s", series_id, e)
            job_status.log_line("download", f"[{title}] 처리 중 오류: {e}")
            failures.append({"title_name": title, "episode_no": None, "subtitle": str(e)})
            return None
        _record_kakao_batch(webtoon, result, failures)
        remaining = _kakao_remaining_free(result)
        if remaining <= 0:
            break
        if batch == _KAKAO_MAX_BATCHES_PER_TITLE:
            job_status.log_line("download", f"[{title}] 한 번에 받을 배치가 너무 많아 여기서 멈춥니다(남은 {remaining}화는 다음 실행에서)")
            break
        rest_minutes = settings.batch_rest_minutes
        job_status.log_line("download", f"[{title}] {len(result.downloaded)}화 받음, 남은 {remaining}화는 {rest_minutes:g}분 쉬었다가 이어받기")
        await asyncio.sleep(rest_minutes * 60)
    return result


async def _download_kakao_subscriptions(settings, failures: list[dict]) -> None:
    """구독 중인 카카오페이지 작품을 폴더 규칙(폴더 없음 → 처음부터 / 파일 여러 개 → 누락 회차 / 파일 1개 → 그 이후)
    으로 받는다(이미 받은 회차는 다시 받지 않는다). 다운로드 스케줄의 대상에 카카오페이지가 들어 있을 때만 불리고,
    로그인 쿠키가 없거나 로그인이 풀려 있으면 받지 않고(풀림은 디스코드로 알림) 건너뛴다."""
    subscribed = repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)
    if not subscribed:
        return

    async with kakao_page_download.download_lock:
        root = download_roots.kakao_root(settings)
        job_status.log_line("download", f"카카오페이지 다운로드 시작 — 구독 중인 웹툰 {len(subscribed)}개 (저장 폴더: {root})")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            client = kakao_page_download.client_from_saved_cookies(session, settings.request_timeout_seconds)
            if client is None:
                job_status.log_line("download", "카카오페이지 로그인 쿠키가 없어서 건너뜁니다(설정에서 쿠키를 저장해주세요)")
                return
            logged_in = await client.check_login()
            status = kakao_page_auth.cookie_status(kakao_page_auth.load_cookies())
            await kakao_page_auth.notify_if_needed(session, settings, logged_in, status["days_left"])
            if logged_in is False:
                job_status.log_line("download", "카카오페이지 로그인이 풀려 있어서 건너뜁니다(쿠키를 다시 붙여넣어 주세요)")
                return

            for webtoon in subscribed:
                result = await _download_kakao_title(client, settings, webtoon, root, failures)
                # 쉬는 시간이 긴 이어받기 중에 쿠키가 갱신될 수 있어서 작품마다 저장한다(중간에 멈춰도 갱신분이 남도록)
                kakao_page_download.persist_refreshed_cookies(client)
                if result is None:
                    continue
                # 완결이고 받을 회차를 다 받았으면 완결 확인 대상으로 기록한다(완결인데 받을 게 남았으면 아직 알리지 않는다).
                # 연재 중으로 돌아왔으면 기록을 되돌려 다음 완결 때 다시 알린다.
                if result.error is None:
                    repository.set_kakao_finished(webtoon["title_id"], result.finished and result.nothing_left)
                await asyncio.sleep(settings.delay_seconds)
        await _notify_kakao_newly_finished()


async def _run_download_job_impl(platforms: set[str]) -> None:
    settings = get_settings()
    active_webtoons = repository.list_by_status(repository.STATUS_ACTIVE) if "naver" in platforms else []

    job_status.start("download")
    if "naver" in platforms:
        job_status.log_line("download", f"다운로드 스캔 시작 — 구독 중인 네이버 웹툰 {len(active_webtoons)}개")
    else:
        job_status.log_line("download", "다운로드 스캔 시작 — 카카오페이지만")
    had_error = False

    adult_tracker = cookie_health.AdultFetchTracker()
    failures: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for webtoon in active_webtoons:
            try:
                await _download_new_episodes_for_one(session, settings, webtoon.title_id, adult_tracker, failures)
            except Exception as e:
                had_error = True
                log.error("웹툰(titleId=%s) 다운로드 처리 중 예외 — 다음 웹툰으로 진행: %s", webtoon.title_id, e)
                job_status.log_line("download", f"[{webtoon.title}] 처리 중 오류: {e}")
                failures.append({"title_name": webtoon.title, "episode_no": None, "subtitle": str(e)})
            # 새 회차가 없어서 그냥 넘어가는 웹툰들 사이에도 딜레이를 둔다 — 예전엔 이
            # 경우에만 딜레이가 전혀 없어서, 구독 웹툰이 많으면 순식간에 연속 요청이
            # 나가는 문제가 있었다(실제로 코드 확인 후 발견됨).
            await asyncio.sleep(settings.delay_seconds)

        try:
            await cookie_health.finalize_and_notify(session, settings, adult_tracker)
        except Exception as e:
            log.error("쿠키 상태 판단/알림 중 예외: %s", e)

        try:
            if "kakao" in platforms:
                await _download_kakao_subscriptions(settings, failures)
        except Exception as e:
            had_error = True
            log.error("카카오페이지 자동 다운로드 중 예외: %s", e)
            job_status.log_line("download", f"카카오페이지 자동 다운로드 중 오류: {e}")

        if failures:
            # 다운로드 리포트가 켜져 있으면 실패 목록이 리포트에도 그대로 포함되므로
            # (동일한 내용이 두 번 오는 게 실제로 불편하다는 피드백 있었음), 이 자리의
            # 즉시 실패 알림은 건너뛴다. 리포트가 꺼져 있으면(설정 안 함) 여기서라도
            # 알려야 사용자가 실패를 알 방법이 없으므로 그대로 보낸다.
            report_configured = schedule_config.get_schedule(
                "report_job", DEFAULT_SCHEDULES["report_job"]
            ).mode != "off"
            if not report_configured:
                try:
                    await _notify_download_failures(session, settings, failures)
                except Exception as e:
                    log.error("실패 요약 알림 전송 중 예외: %s", e)

    try:
        _cleanup_old_episode_history()
    except Exception as e:
        log.error("다운로드 이력 보관기간 정리 중 예외: %s", e)

    try:
        _cleanup_old_job_history()
    except Exception as e:
        log.error("실행 이력 보관기간 정리 중 예외: %s", e)

    job_status.log_line("download", "다운로드 스캔 종료")
    job_status.finish("download", success=not had_error)


def _cleanup_old_job_history() -> None:
    """설정된 보관 기간(일)이 있으면, 그보다 오래된 실행 이력(수동실행/신작스캔/다운로드
    등 잡 실행 기록)을 정리한다. 설정 안 했으면 아무것도 안 함."""
    retention_raw = repository.get_setting("job_history_retention_days")
    if not retention_raw:
        return
    retention_days = int(retention_raw)
    if retention_days <= 0:
        return
    deleted = repository.delete_job_history_older_than(retention_days)
    if deleted:
        job_status.log_line("download", f"보관기간({retention_days}일) 초과 실행 이력 {deleted}건 정리")


def _cleanup_old_episode_history() -> None:
    """설정된 보관 기간(일)이 있으면, 그보다 오래된 다운로드 이력을 정리한다
    (파일은 그대로 유지됨). 설정 안 했으면(0 또는 미설정) 아무것도 안 함."""
    retention_raw = repository.get_setting("episode_history_retention_days")
    if not retention_raw:
        return
    retention_days = int(retention_raw)
    if retention_days <= 0:
        return
    deleted = repository.delete_episode_history_older_than(retention_days)
    if deleted:
        job_status.log_line("download", f"보관기간({retention_days}일) 초과 다운로드 이력 {deleted}건 정리")


def _cleanup_old_archive_history() -> None:
    """설정된 보관 기간(일)이 있으면, 그보다 오래된 아카이빙 이력을 정리한다
    (실제로 옮겨진 파일은 그대로 유지됨). 설정 안 했으면 아무것도 안 함."""
    retention_raw = repository.get_setting("archive_history_retention_days")
    if not retention_raw:
        return
    retention_days = int(retention_raw)
    if retention_days <= 0:
        return
    deleted = repository.delete_archive_history_older_than(retention_days)
    if deleted:
        job_status.log_line("archive", f"보관기간({retention_days}일) 초과 아카이빙 이력 {deleted}건 정리")


async def _notify_archive_issues(
    session: aiohttp.ClientSession, settings: Settings, conflicts: list[tuple[str, str, str]], failures: list[tuple[str, str, str]]
) -> None:
    """주기 아카이빙(완결 자동이동 포함) 실행 중 이름 충돌을 처리했거나 실패한 게
    있으면 한 번에 모아서 디스코드로 알린다 — 다운로드 실패 요약과 같은 패턴
    (건마다 알리면 스팸이 되니 실행 끝날 때 요약 1건)."""
    policy_label = {"skip": "건너뜀", "overwrite": "덮어씀", "rename": "이름 바꿔 저장"}
    lines = ["📦 **아카이빙 실행 알림**"]
    if conflicts:
        lines.append(f"⚠️ 파일명 충돌 {len(conflicts)}건")
        for title_name, file_name, policy in conflicts:
            lines.append(f"- {title_name}: {file_name} ({policy_label.get(policy, policy)})")
    if failures:
        lines.append(f"❌ 이동 실패 {len(failures)}건")
        for title_name, file_name, error in failures:
            lines.append(f"- {title_name}: {file_name} — {error}")
    await discord_notify.send_webhook_notification(session, settings, "\n".join(lines))


async def _notify_download_failures(
    session: aiohttp.ClientSession, settings: Settings, failures: list[dict]
) -> None:
    """이번 실행에서 실패한 웹툰/회차를 한 번에 모아서 디스코드로 보낸다 — 실패마다
    따로 알림을 보내면 스팸이 되니, 실행이 끝날 때 요약 1건으로 보낸다."""
    lines = [f"⚠️ **다운로드 실패 {len(failures)}건** (이번 실행)"]
    for f in failures:
        if f["episode_no"] is not None:
            lines.append(f"- {f['title_name']} {f['episode_no']}화 \"{f['subtitle']}\"")
        else:
            lines.append(f"- {f['title_name']}: {f['subtitle']}")
    await discord_notify.send_webhook_notification(session, settings, "\n".join(lines))


_SETTING_KEY_REPORT_LAST_SENT_AT = "report_last_sent_at"
_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"
_REPORT_LIST_LIMIT = 40  # 디스코드 메시지 길이 제한 대비, 항목이 너무 많으면 일부만 나열
_UNREGISTERED_NEW_EPISODE_LIMIT = 20  # 미등록 신규 에피소드는 네이버에 회차번호를 하나씩 더 물어봐야 해서, 너무 많이 걸면 부담이라 따로 더 작게 제한
# 카카오는 "웹툰 전체목록"에 있는(=요일 7개, 제외 안 한) 것 전부가 후보 대상이라
# 네이버의 "완전 미등록"보다 모수가 훨씬 크다 — UP 표시된 게 20개를 넘는 날은 실제로
# 흔해서, 위 한도를 그대로 쓰면 뒤로 밀린 작품(카탈로그에서 나중에 나온 것)의 새
# 에피소드가 후보에도 못 들고 조용히 빠지는 문제가 있었다(실제로 확인됨: "바퀴벌레
# 잔혹사"가 UP인데 리포트엔 안 나옴). 조회 자체(뷰어/카카오 최신 회차 확인)는 이미
# 동시 실행 수 제한(artist_scan_concurrency)이 있어서 이 한도를 넉넉히 키워도
# 부담이 크지 않고, 실제 디스코드 메시지에 보여줄 개수는 아래 _build_report_message
# 에서 _REPORT_LIST_LIMIT로 별도로 줄인다(둘을 분리해야 "후보에서 누락"과 "메시지에서
# 생략 표시하며 자름"이 다른 문제라는 게 명확해진다).
_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT = 150


def _log_unchecked_adult(platform_label: str, titles: list[str], how_to_fix: str) -> None:
    """성인 작품의 새 회차를 쿠키 문제로 확인하지 못했을 때 조용히 넘기지 않고 리포트 실행 로그에 남긴다(리포트 메시지에는 넣지 않는다)."""
    if not titles:
        return
    shown = ", ".join(titles[:5]) + (" 등" if len(titles) > 5 else "")
    message = f"{platform_label} 성인 작품 {len(titles)}개는 새 에피소드를 확인하지 못했습니다({shown}) — {how_to_fix}"
    log.warning(message)
    job_status.log_line("report", message)


async def _collect_unregistered_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[str, str, int]]:
    """네이버 "웹툰 전체목록"에 보이는 **미구독** 작품 중 새 에피소드(UP 표시)가 있는 것만 골라서
    (title_id, title_name, 최신 회차 번호)로 반환한다(카카오 _collect_kakao_new_episodes와 같은 규칙).
    미구독 = 기록이 없거나, 구독한 적이 있지만 "목록으로" 되돌려 "목록" 상태인 작품. 구독 중인 작품은
    다운로드로 알리고, 구독해제/제외됨은 "안 챙겨본다"는 뜻이라 알리지 않는다. 구독을 안 해서 놓치고
    있었을 수도 있는 신작을 리포트에서 바로 발견하게 해 주는 용도라, 없는 게 정상인 경우가 대부분이고
    실패해도 리포트 자체는 계속 보내야 한다."""
    try:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except Exception as e:
        log.error("미등록 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    def _not_subscribed_in_list(title_id: str) -> bool:
        record = repository.get(title_id)
        return record is None or record.status == repository.STATUS_UNREGISTERED

    candidates = [item for item in items if item.has_update and _not_subscribed_in_list(item.title_id)]
    candidates = candidates[:_UNREGISTERED_NEW_EPISODE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 성인 작품은 성인 인증 쿠키가 없으면 회차 목록이 비어서 와서 최신 회차를 알 수 없다 — 다운로드와 같은 쿠키로 조회한다
    adult_cookies = get_adult_cookies(settings.cookie_file_path)

    async def _fetch_one(item):
        async with semaphore:
            try:
                episode_no = await naver_api.fetch_latest_episode_no(
                    session, item.title_id, settings.request_timeout_seconds, cookies=adult_cookies if item.is_adult else None,
                )
            except Exception as e:
                log.error("미등록 작품(titleId=%s) 최신 회차 조회 실패(건너뜀): %s", item.title_id, e)
                episode_no = None
            await asyncio.sleep(settings.delay_seconds)
            return item, episode_no

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "네이버", [item.title_name for item, episode_no in results if episode_no is None and item.is_adult],
        "성인 인증 쿠키 파일(COOKIE_DIR_HOST_PATH)이 없거나 만료됐을 수 있습니다",
    )
    return [
        (item.title_id, item.title_name, episode_no)
        for item, episode_no in results
        if episode_no is not None
    ]


async def _collect_kakao_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[int, str, str]]:
    """네이버의 _collect_unregistered_new_episodes와 같은 취지로, 카카오페이지 "웹툰 전체목록"에 있는 **미구독** 웹툰 중 새 회차(UP
    표시)가 있는 것만 골라 (title_id, title_name, 카카오페이지 바로가기 URL)로 반환한다. 구독을 안 해서 놓치고 있던 신작을 리포트에서
    발견하게 해 주는 용도다.

    구독 중인 작품은 이 앱이 직접 받아서 "받은 작품"에 나오므로 여기서는 뺀다(예전엔 카카오 "구독"이 웹툰 뷰어에 있다는 표시일 뿐이라
    구독 중인 작품도 알려 줬지만, 지금은 중복 알림이다). 구독해제/제외됨도 "더 이상 안 챙겨본다"는 뜻이라 뺀다. 목록에 그대로 보이는
    "목록" 상태와 아직 아무 기록이 없는 작품만 후보로 남는다. 구독하지 않은 작품은 웹툰 뷰어에 없으니 링크는 항상 카카오페이지 것이다."""
    try:
        # 리포트는 화면 캐시(10분)가 아니라 그 시점의 최신 목록으로 만든다
        items = await kakao_api.fetch_weekday_catalog(session, settings.request_timeout_seconds, use_cache=False)
    except Exception as e:
        log.error("카카오 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    skipped_ids = (
        repository.get_kakao_excluded_title_ids()
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_UNSUBSCRIBED)}
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)}
    )
    candidates = [item for item in items if item["has_update"] and item["title_id"] not in skipped_ids]
    candidates = candidates[:_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 19세 작품은 로그인한 계정으로 조회한다(네이버 성인 작품과 같은 규칙) — 저장된 카카오페이지 로그인 쿠키가 없으면 로그인 없이 시도
    saved = kakao_page_auth.load_cookies()
    login_cookies = kakao_page_auth.cookie_map(saved) if saved else None

    async def _fetch_one(item):
        async with semaphore:
            try:
                url = await kakao_api.fetch_latest_episode_url(
                    session, item["title_id"], settings.request_timeout_seconds, cookies=login_cookies if item["is_adult"] else None,
                )
            except Exception as e:
                log.error("카카오 작품(title_id=%s) 최신 회차 조회 실패(건너뜀): %s", item["title_id"], e)
                url = None
            await asyncio.sleep(settings.delay_seconds)
            return item, url

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "카카오", [item["title_name"] for item, url in results if url is None and item["is_adult"]],
        "카카오페이지 로그인 쿠키가 없거나 만료됐을 수 있습니다(설정 > 카카오웹툰 관리)",
    )
    final = [(item["title_id"], item["title_name"], url) for item, url in results if url is not None]
    if len(final) < len(candidates):
        # 후보였는데 최종 리포트엔 안 실린 개수 — 조회 실패(HTTP 403 등)로 조용히
        # 빠진 게 몇 개인지 여기서 한눈에 보이게 남겨둔다. 원인 자체는 위
        # fetch_latest_episode_url/조회 실패 경고 로그에 있지만, 매번 그걸 하나하나
        # 찾아보지 않아도 "얼마나 빠졌는지"는 바로 알 수 있어야 한다.
        log.warning(
            "카카오 새 에피소드 후보 %d개 중 %d개만 최종 리포트에 포함됨(나머지는 회차 조회 실패)",
            len(candidates), len(final),
        )
    return final


def _build_report_message(
    success_rows: list[dict], failed_rows: list[dict], reader_urls: dict[str, str],
    unregistered_new_episodes: list[tuple[str, str, int]] | None = None,
    app_public_base_url: str = "",
    kakao_new_episodes: list[tuple[int, str, str]] | None = None,
) -> str:
    """예전 hermes webtoon_checker.py의 메시지 구조(다운로드됨/실패)를 그대로 따른다.
    모든 링크는 <...>로 감싸서 디스코드가 미리보기(임베드)를 안 만들게 한다 — 링크가
    여러 개 나열될 때 임베드가 줄줄이 생겨서 메시지가 너무 길어지는 문제가 있었다."""
    today_label = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    # 카카오페이지 이력이 섞여 있으면 어느 플랫폼인지 앞에 붙이고, 카카오는 받은 회차 수/범위와(뷰어에 없으면) 카카오페이지 링크를 보여준다.
    # 네이버만 있을 땐 예전 형식 그대로다. reader_urls의 카카오 키는 "kakao:제목"이다.
    tag_platforms = any(r.get("platform") == "kakao" for r in success_rows + failed_rows)
    success_groups: dict[tuple[str, str], list[dict]] = {}
    for r in success_rows:
        success_groups.setdefault((r.get("platform") or "naver", r["title_name"]), []).append(r)
    success_titles = sorted(success_groups, key=lambda key: (key[0] != "naver", key[1]))
    success_lines = []
    for platform, title in success_titles:
        prefix = f"[{'카카오' if platform == 'kakao' else '네이버'}] " if tag_platforms else ""
        if platform == "kakao":
            group = success_groups[(platform, title)]
            numbers = sorted({r["episode_no"] for r in group})
            span = f"{numbers[0]}번" if len(numbers) == 1 else f"{numbers[0]}~{numbers[-1]}번"
            url = reader_urls.get(f"kakao:{title}") or f"https://page.kakao.com/content/{group[0]['title_id']}"
            success_lines.append(f"• {prefix}{title} · {len(numbers)}개 회차({span}) [바로가기](<{url}>)")
        else:
            url = reader_urls.get(title)
            success_lines.append(f"• {prefix}{title} [바로가기](<{url}>)" if url else f"• {prefix}{title}")

    failed_keys = sorted({(r.get("platform") or "naver", r["title_name"]) for r in failed_rows}, key=lambda key: (key[0] != "naver", key[1]))
    failed_titles = [f"[{'카카오' if p == 'kakao' else '네이버'}] {t}" if tag_platforms else t for p, t in failed_keys]

    parts = [
        f"📅 웹툰 다운로드 리포트 ({today_label})",
        "",
        f"📁 받은 작품 ({len(success_titles)}):",
        "\n".join(success_lines[:_REPORT_LIST_LIMIT]) if success_lines else "없음",
    ]
    if len(success_lines) > _REPORT_LIST_LIMIT:
        parts.append(f"_외 {len(success_lines) - _REPORT_LIST_LIMIT}개 생략_")

    if failed_titles:
        parts.extend([
            "",
            f"❌ 실패한 작품 ({len(failed_titles)}):",
            "\n".join(failed_titles[:_REPORT_LIST_LIMIT]),
        ])
        if len(failed_titles) > _REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(failed_titles) - _REPORT_LIST_LIMIT}개 생략_")

    new_lines = []
    for title_id, title, episode_no in unregistered_new_episodes or []:
        read_url = f"{NAVER_DETAIL_URL_TEMPLATES['webtoon']}?titleId={title_id}&no={episode_no}"
        line = f"• [네이버] {title} [바로가기](<{read_url}>)"
        if app_public_base_url:
            exclude_url = f"{app_public_base_url}/api/webtoons/{title_id}/exclude-confirm?title={quote(title)}"
            line += f" · [목록 제외](<{exclude_url}>)"
        new_lines.append(line)
    for title_id, title, url in kakao_new_episodes or []:
        line = f"• [카카오] {title} [바로가기](<{url}>)"
        if app_public_base_url:  # 여기 나오는 건 전부 미구독 작품이라 "목록 제외" 링크를 붙인다
            exclude_url = f"{app_public_base_url}/api/kakao-webtoons/{title_id}/exclude-confirm?title={quote(title)}"
            line += f" · [목록 제외](<{exclude_url}>)"
        new_lines.append(line)
    if new_lines:
        parts.extend([
            "",
            f"🆕 웹툰 전체목록 중 새 에피소드 ({len(new_lines)}):",
            "\n".join(new_lines[:_REPORT_LIST_LIMIT]),
        ])
        if len(new_lines) > _REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(new_lines) - _REPORT_LIST_LIMIT}개 생략_")

    return "\n".join(parts)


async def run_report_job(force_test: bool = False) -> None:
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report_job_impl(force_test=force_test)


async def run_archive_job() -> None:
    if _archive_job_lock.locked():
        job_status.log_line("archive", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _archive_job_lock:
        settings = get_settings()
        job_status.start("archive")
        try:
            update_result = await rclone_updater.check_and_update()
            job_status.log_line("archive", update_result)
        except Exception as e:
            log.error("rclone 자동 업데이트 확인 중 예외: %s", e)
            job_status.log_line("archive", f"rclone 업데이트 확인 중 오류(무시하고 계속): {e}")
        conflicts: list[tuple[str, str, str]] = []
        failures: list[tuple[str, str, str]] = []
        try:
            moved = await asyncio.to_thread(
                archiver.run_periodic_archive, settings.archive_root, download_roots.naver_root(settings), settings.rclone_config_path,
                lambda msg: job_status.log_line("archive", msg), conflicts, failures,
                download_roots.kakao_root(settings),  # 카카오페이지 웹툰 대상은 카카오 다운로드 폴더에서 옮긴다
            )
            job_status.log_line("archive", f"지정 웹툰 {moved}개 파일 이동 완료")

            pending_moved = await asyncio.to_thread(
                archiver.process_pending_finish_archives, settings.archive_root, download_roots.naver_root(settings), settings.rclone_config_path,
                lambda msg: job_status.log_line("archive", msg), conflicts, failures, download_roots.kakao_root(settings),
            )
            job_status.log_line("archive", f"완결 구독해제 대기열 {pending_moved}개 파일 이동 완료")
            _cleanup_old_archive_history()

            if conflicts or failures:
                async with aiohttp.ClientSession() as session:
                    await _notify_archive_issues(session, settings, conflicts, failures)

            job_status.finish("archive", success=True)
        except Exception as e:
            log.error("아카이빙 잡 중 예외: %s", e)
            job_status.log_line("archive", f"오류: {e}")
            job_status.finish("archive", success=False)


_KST = ZoneInfo("Asia/Seoul")


def _kst_day_range_utc(day) -> tuple[str, str]:
    """한국시간 하루(day)의 [시작, 끝)을 UTC ISO 문자열로."""
    start_kst = datetime.combine(day, datetime.min.time(), tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _select_report_rows(since: str | None, force_test: bool) -> tuple[list[dict], bool]:
    """리포트에 담을 다운로드 이력과, 어제 기록으로 대체했는지(테스트 발송에서만).

    일반 발송은 "지난 발송 이후" 이력 전부다. 테스트 발송은 그 누적 로직을 아예 안 쓴다 — 오늘(한국시간) 다운로드한 것만 보내고,
    오늘 게 없으면 어제 것만 보낸다. 전체 이력이 몰려서 나오는 걸 막기 위해 날짜 하루 단위로 딱 끊는다."""
    if not force_test:
        return (repository.list_episode_history_since(since) if since else []), False
    today = datetime.now(_KST).date()
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today))
    if rows:
        return rows, False
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today - timedelta(days=1)))
    return rows, bool(rows)


async def _collect_new_episode_sections(session: aiohttp.ClientSession, settings, *, force_test: bool) -> tuple[list, list]:
    """등록 안 한 작품의 새 에피소드(네이버, 카카오 순). 다운로드 기록과 무관하게(rows가 비어 있어도) 항상 확인한다 — 구독을 안 해서
    기록 자체가 없는 작품을 발견하는 게 이 섹션의 목적이라, "받은 게 없으니 리포트도 없음"에 묻히면 안 된다. 일반 발송에서는 이미
    같은 회차로 알린 작품을 뺀다(UP 표시가 남아 있는 동안 리포트마다 반복되지 않게) — 테스트 발송은 전부 보여준다."""
    enabled = repository.get_setting("report_unregistered_new_episodes_enabled") != "0"
    kakao_enabled = repository.get_setting("kakao_webtoons_enabled") == "1"
    naver_items = await _collect_unregistered_new_episodes(session, settings) if enabled else []
    kakao_items = await _collect_kakao_new_episodes(session, settings) if enabled and kakao_enabled else []
    if not force_test:
        naver_items, kakao_items = report_seen.filter_unseen(naver_items, kakao_items)
    return naver_items, kakao_items


async def _resolve_reader_urls(session: aiohttp.ClientSession, settings, success_rows: list[dict]) -> dict[str, str]:
    """받은 작품의 웹툰 뷰어 바로가기 링크(뷰어 서버가 설정돼 있을 때만). 키: 네이버는 제목, 카카오는 "kakao:제목".
    웹툰서버는 실제 디스크 폴더명 기준으로 매칭하는데, 폴더를 만들 때는 ':' 같은 금지문자를 치환해서 저장한다 — 네이버는 전각 문자로
    (예: "제목 : 부제" → "제목 ： 부제"), 카카오는 밑줄 규칙으로. 원본 제목을 그대로 조회하면 문자가 안 맞아 실패한 사례가 있어서
    폴더명 생성과 같은 치환을 거쳐 조회한다."""
    webtoon_server_url = repository.get_setting(_SETTING_KEY_WEBTOON_SERVER_URL) or ""
    urls: dict[str, str] = {}
    if not webtoon_server_url:
        return urls
    for platform, folder_name, key_prefix in (("naver", remove_forbidden_str, ""), ("kakao", remove_forbidden_str_kakao, "kakao:")):
        for title in sorted({r["title_name"] for r in success_rows if (r.get("platform") or "naver") == platform}):
            url = await webtoon_server_client.fetch_reader_url(session, webtoon_server_url, folder_name(title), settings.request_timeout_seconds)
            if url:
                urls[f"{key_prefix}{title}"] = url
    return urls


async def _run_report_job_impl(force_test: bool = False) -> None:
    settings = get_settings()
    job_status.start("report")

    since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not since and not force_test:
        # 최초 실행이면 지금까지 쌓인 이력을 전부 몰아 보내는 대신, 지금 시점부터 집계를 시작한다 — 첫 리포트에 예전 이력이 전부
        # 딸려오는 걸 방지. 수동 테스트(force_test)일 때는 이 규칙을 건너뛴다 — 사용자가 실제 발송 형태를 확인해보고 싶은 것이므로.
        repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", "최초 실행 — 이번 시점부터 집계 시작 (발송 없음)")
        job_status.finish("report", success=True)
        return

    rows, used_fallback = _select_report_rows(since, force_test)
    if not force_test:
        rows = report_seen.filter_unseen_rows(rows)  # 이미 보고한 회차는 뺀다(같은 회차는 한 번만) — 테스트 발송은 전부 보여준다
    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    app_public_base_url = repository.get_setting("app_public_base_url") or ""

    try:
        async with aiohttp.ClientSession() as session:
            unregistered_new_episodes, kakao_new_episodes = await _collect_new_episode_sections(session, settings, force_test=force_test)
            if not rows and not unregistered_new_episodes and not kakao_new_episodes:
                job_status.log_line("report", "발송할 내용 없음 (다운로드 기록도, 새 에피소드도 없음)")
                if not force_test:
                    repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
                job_status.finish("report", success=True)
                return

            reader_urls = await _resolve_reader_urls(session, settings, success_rows)
            message = _build_report_message(
                success_rows, failed_rows, reader_urls, unregistered_new_episodes, app_public_base_url, kakao_new_episodes
            )
            if used_fallback:
                message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
            await discord_notify.send_webhook_notification(session, settings, message)
            if not force_test:
                # 전송이 성공한 뒤에만 기록(실패하면 다음에 다시 시도)
                report_seen.remember(unregistered_new_episodes, kakao_new_episodes)
                report_seen.remember_rows(rows)

        if not force_test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {len(success_rows)}건, 실패 {len(failed_rows)}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)


async def _notify_kakao_newly_finished() -> None:
    """완결이고 받을 회차를 다 받았는데 아직 알리지 않은 구독 중인 카카오 작품에 완결 확인 메시지(구독해제/알람 제외 버튼)를 보낸다.
    봇이 꺼져 있거나 전송이 실패하면 알림 기록을 남기지 않아 다음 실행 때 다시 시도한다."""
    sent = 0
    for wt in repository.list_kakao_finish_pending():
        try:
            if await discord_bot.send_completion_prompt(str(wt["title_id"]), wt["title"], platform="kakao"):
                repository.set_kakao_finish_notified(wt["title_id"])
                sent += 1
        except Exception as e:
            log.error("카카오 완결 알림 전송 중 예외 (series_id=%s): %s", wt["title_id"], e)
    if sent:
        job_status.log_line("download", f"카카오페이지 완결 확인 알림 {sent}건 전송")


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


_JOB_FUNCS = {
    "discovery_job": run_discovery_job,
    "report_job": run_report_job,
    "archive_job": run_archive_job,
}


def _build_trigger(schedule: JobSchedule):
    """off면 None(=스케줄 없음), interval이면 N분마다, cron이면 지정한 시:분들/요일에.
    hour/minute은 한국시간 기준으로 해석된다(스케줄러 자체가 Asia/Seoul로 고정됨).
    시각을 여러 개 지정할 수 있어서(예: 07:00과 19:30 둘 다), 시각마다 CronTrigger를
    하나씩 만들고 OrTrigger로 묶는다 — hour/minute을 각각 콤마로 나열하면(예:
    hour='7,19', minute='0,30') 두 필드가 독립적으로 평가되어 07:30/19:00처럼
    의도하지 않은 조합까지 걸리므로, 반드시 각 시각을 별개 트리거로 만들어야 한다."""
    if schedule.mode == "off":
        return None
    if schedule.mode == "cron":
        day_of_week = ",".join(schedule.cron_days) if schedule.cron_days else "*"
        triggers = [
            CronTrigger(hour=t["hour"], minute=t["minute"], day_of_week=day_of_week, timezone="Asia/Seoul")
            for t in schedule.cron_times
        ]
        if len(triggers) == 1:
            return triggers[0]
        return OrTrigger(triggers)
    return IntervalTrigger(minutes=schedule.interval_minutes)


def _apply_download_schedules(scheduler: AsyncIOScheduler) -> None:
    """다운로드 잡은 스케줄이 여러 개(각각 대상이 다름)라서, 등록된 것을 전부 지우고 저장된 목록대로 다시 등록한다."""
    for job in scheduler.get_jobs():
        if job.id == "download_job" or job.id.startswith("download_job:"):
            scheduler.remove_job(job.id)
    for index, entry in enumerate(schedule_config.get_download_schedules(DEFAULT_SCHEDULES["download_job"])):
        trigger = _build_trigger(entry)
        if trigger is not None:
            scheduler.add_job(run_download_job, trigger=trigger, id=f"download_job:{index}", kwargs={"target": entry.target})


def _apply_job_schedule(scheduler: AsyncIOScheduler, job_id: str) -> None:
    if job_id == "download_job":
        _apply_download_schedules(scheduler)
        return
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


async def run_kakao_catalog_refresh_job() -> None:
    """카카오페이지 전체목록 캐시를 3시간마다 백그라운드로 새로 채운다(카카오 기능이 켜져 있을 때만)."""
    if repository.get_setting("kakao_webtoons_enabled") != "1":
        return
    try:
        await kakao_catalog.refresh()
    except Exception as e:
        log.error("카카오 목록 정기 새로고침 중 예외: %s", e)


def create_scheduler() -> AsyncIOScheduler:
    # 컨테이너의 시스템 시간대(보통 UTC)와 무관하게 항상 한국시간으로 해석하도록
    # 명시한다 — 명시 안 하면 APScheduler가 컨테이너의 시스템 기본값(UTC)을 쓰는데,
    # 설정 화면에서 "07:00"이라고 넣으면 사용자는 한국시간 07:00을 기대하지만
    # 실제로는 UTC 07:00(한국시간 오후 4시)에 실행되고 있었다 — 실제로 확인된 버그.
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
    # 사용자가 설정하는 스케줄이 아니라 고정된 내부 작업 — 설정 화면의 스케줄 저장/재등록과 무관하게 항상 돈다
    scheduler.add_job(
        run_kakao_catalog_refresh_job, trigger=IntervalTrigger(minutes=kakao_catalog.REFRESH_INTERVAL_MINUTES),
        id="kakao_catalog_refresh",
    )
    return scheduler


def reschedule_all(scheduler: AsyncIOScheduler) -> None:
    for job_id in DEFAULT_SCHEDULES:
        _apply_job_schedule(scheduler, job_id)
