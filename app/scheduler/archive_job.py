"""아카이빙 작업 — 대상별 아카이빙 실행, 아카이빙 이력 정리, 문제 알림.
"""

import asyncio
import logging

import aiohttp

from app import archiver, discord_notify, download_roots, job_status, rclone_updater, repository, webtoon_server_client
from app.archiver.title_archive import ReadNumbersProvider
from app.config import get_settings
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao

log = logging.getLogger(__name__)

_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"
_archive_job_lock = asyncio.Lock()


def _build_read_numbers_provider(
    loop: asyncio.AbstractEventLoop, session: aiohttp.ClientSession, server_url: str, timeout_seconds: int
) -> ReadNumbersProvider:
    """아카이빙은 별도 스레드에서 도는 동기 코드라, 뷰어 조회(비동기)는 이 잡의 이벤트 루프에 맡겨 결과를 기다리는 동기 함수로 감싼다.
    뷰어는 디스크 폴더명 기준으로 작품을 찾으므로 금지문자 치환은 폴더를 만들 때와 같은 규칙(네이버/카카오)을 쓴다."""
    def provider(title_name: str, kakao: bool) -> frozenset[int] | None:
        folder_name = (remove_forbidden_str_kakao if kakao else remove_forbidden_str)(title_name)
        future = asyncio.run_coroutine_threadsafe(
            webtoon_server_client.fetch_read_numbers(session, server_url, folder_name, timeout_seconds), loop
        )
        return future.result()
    return provider


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
    session: aiohttp.ClientSession, conflicts: list[tuple[str, str, str]], failures: list[tuple[str, str, str]]
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
    await discord_notify.send_webhook_notification(session, "\n".join(lines))


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
            server_url = await asyncio.to_thread(repository.get_setting, _SETTING_KEY_WEBTOON_SERVER_URL)
            async with aiohttp.ClientSession() as viewer_session:
                provider = (  # 뷰어 서버가 설정돼 있을 때만 — 뷰어에서 읽지 않은 회차는 옮기지 않는다(조회 실패한 작품은 거르지 않음)
                    _build_read_numbers_provider(asyncio.get_running_loop(), viewer_session, server_url, settings.request_timeout_seconds)
                    if server_url else None
                )
                moved = await asyncio.to_thread(
                    archiver.run_periodic_archive, settings.archive_root, download_roots.naver_root(settings), settings.rclone_config_path,
                    lambda msg: job_status.log_line("archive", msg), conflicts, failures,
                    download_roots.kakao_root(settings),  # 카카오페이지 웹툰 대상은 카카오 다운로드 폴더에서 옮긴다
                    provider,
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
                    await _notify_archive_issues(session, conflicts, failures)

            job_status.finish("archive", success=True)
        except Exception as e:
            log.error("아카이빙 잡 중 예외: %s", e)
            job_status.log_line("archive", f"오류: {e}")
            job_status.finish("archive", success=False)
