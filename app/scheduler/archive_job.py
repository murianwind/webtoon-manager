"""아카이빙 작업 — 대상별 아카이빙 실행, 아카이빙 이력 정리, 문제 알림.
"""

import asyncio
from contextlib import asynccontextmanager
import logging

import aiohttp

from app import archiver, discord_notify, download_roots, job_status, rclone_updater, repository, retention, viewer_scan, webtoon_server_client
from app.archiver import cancel
from app.archiver.failure_reason import summarize as summarize_failure
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


_MAX_LOOKUP_LINES = 30  # 실행 이력에 작품별로 풀어서 남기는 조회 수(나머지는 개수만)


def _record_read_lookups(source: str, lookups: list[tuple[str, bool, "frozenset[int] | None"]]) -> None:
    """이번 실행에서 뷰어 서버에 읽은 회차를 조회한 결과를 실행 이력("웹툰 서버 연동") 한 건으로 남긴다 — 작품별로 읽은 회차 수 또는 조회 실패(옮기지 않음). 조회가 없었으면 남기지 않는다."""
    if not lookups:
        return
    failed = sum(1 for _, _, result in lookups if result is None)
    lines = [f"{source} — 웹툰 서버에 읽은 회차를 조회했습니다 ({len(lookups)}개 작품: 성공 {len(lookups) - failed}, 실패 {failed})"]
    for title_name, kakao, result in lookups[:_MAX_LOOKUP_LINES]:
        label = f"[카카오] {title_name}" if kakao else title_name
        lines.append(f"- {label}: " + ("조회 실패 → 옮기지 않음" if result is None else f"읽은 회차 {len(result)}개"))
    if len(lookups) > _MAX_LOOKUP_LINES:
        lines.append(f"… 외 {len(lookups) - _MAX_LOOKUP_LINES}개 작품")
    job_status.record_event("viewer_server", lines, success=failed == 0)


@asynccontextmanager
async def read_numbers_provider_for(settings, source: str = "아카이빙"):
    """뷰어 서버 주소가 설정돼 있으면 읽은 회차를 묻는 조회 함수(동기)를, 아니면 None을 준다 — 정기 아카이빙과 "지금 이동"(수동)이 같이 쓴다. 뷰어 세션은 이 블록이 끝나면 닫힌다.
    조회한 결과는 블록이 끝날 때(오류로 끝나도) 실행 이력에 한 건으로 남긴다(source: 정기 아카이빙/지금 이동(수동))."""
    server_url = await asyncio.to_thread(repository.get_setting, _SETTING_KEY_WEBTOON_SERVER_URL)
    if not server_url:
        yield None
        return
    lookups: list[tuple[str, bool, frozenset[int] | None]] = []
    async with aiohttp.ClientSession() as viewer_session:
        inner = _build_read_numbers_provider(asyncio.get_running_loop(), viewer_session, server_url, settings.request_timeout_seconds)

        def provider(title_name: str, kakao: bool) -> frozenset[int] | None:
            result = inner(title_name, kakao)
            lookups.append((title_name, kakao, result))
            return result

        try:
            yield provider
        finally:
            _record_read_lookups(source, lookups)


def _cleanup_old_archive_history() -> None:
    """설정된 보관 기간(일)보다 오래된 아카이빙 이력을 정리한다(실제로 옮겨진 파일은 그대로 유지됨). 설정 안 했으면 아무것도 안 함."""
    retention.cleanup_history("archive", "archive_history_retention_days", repository.delete_archive_history_older_than, "아카이빙 이력")


_MAX_CONFLICT_LINES = 5  # 알림에 풀어서 보여 주는 충돌 수(나머지는 "외 N건")
_MAX_REASON_LINES = 5  # 알림에 보여 주는 실패 사유 가짓수(나머지는 한 줄로)
_MAX_EXAMPLES_PER_REASON = 3  # 사유마다 보여 주는 예(제목: 파일) 수
_MAX_MESSAGE_LENGTH = 1900  # 디스코드 글자 제한(2000자) 안에 들어오게 하는 한도
_LOG_POINTER = "🔎 자세한 오류는 앱의 아카이빙 로그(수동 실행 화면 · 설정의 실행 이력)에서 확인하세요."


def build_archive_issue_message(conflicts: list[tuple[str, str, str]], failures: list[tuple[str, str, str]]) -> str:
    """아카이빙 실행 알림 본문. 충돌은 파일마다 한 줄(5건까지), 실패는 **한 줄 사유로 줄여서 같은 사유끼리 묶는다**(사유마다 예 3개까지) — rclone 오류 전문(여러 줄)을 파일마다 싣지 않는다.
    자세한 오류는 실행 로그에 그대로 남아 있고, 알림 끝에서 로그를 보라고 안내한다. 어떤 입력이어도 디스코드 글자 제한 안에 들어온다."""
    policy_label = {"skip": "건너뜀", "overwrite": "덮어씀", "rename": "이름 바꿔 저장"}
    lines = ["📦 **아카이빙 실행 알림**"]
    if conflicts:
        lines.append(f"⚠️ 파일명 충돌 {len(conflicts)}건")
        for title_name, file_name, policy in conflicts[:_MAX_CONFLICT_LINES]:
            lines.append(f"- {title_name}: {file_name} ({policy_label.get(policy, policy)})")
        if len(conflicts) > _MAX_CONFLICT_LINES:
            lines.append(f"- 외 {len(conflicts) - _MAX_CONFLICT_LINES}건")
    if failures:
        by_reason: dict[str, list[tuple[str, str]]] = {}
        for title_name, file_name, error in failures:
            by_reason.setdefault(summarize_failure(error), []).append((title_name, file_name))  # 처음 나온 순서를 지킨다
        lines.append(f"❌ 이동 실패 {len(failures)}건")
        reasons = list(by_reason.items())
        for reason, items in reasons[:_MAX_REASON_LINES]:
            examples = ", ".join(f"{title_name}: {file_name}" for title_name, file_name in items[:_MAX_EXAMPLES_PER_REASON])
            more = f" 외 {len(items) - _MAX_EXAMPLES_PER_REASON}건" if len(items) > _MAX_EXAMPLES_PER_REASON else ""
            lines.append(f"- {reason} ({len(items)}건): {examples}{more}")
        if len(reasons) > _MAX_REASON_LINES:
            rest = reasons[_MAX_REASON_LINES:]
            lines.append(f"- 그 밖의 사유 {len(rest)}가지 ({sum(len(items) for _, items in rest)}건)")
        lines.append(_LOG_POINTER)
    message = "\n".join(lines)
    if len(message) > _MAX_MESSAGE_LENGTH:  # 제목/파일 이름이 아주 길 때의 안전장치 — 안내 줄은 남기고 중간을 줄인다
        tail = ("\n" + _LOG_POINTER) if failures else ""
        message = message[: _MAX_MESSAGE_LENGTH - len(tail) - 1] + "…" + tail
    return message


async def _notify_archive_issues(
    session: aiohttp.ClientSession, conflicts: list[tuple[str, str, str]], failures: list[tuple[str, str, str]]
) -> None:
    """주기 아카이빙(완결 자동이동 포함) 실행 중 이름 충돌을 처리했거나 실패한 게
    있으면 한 번에 모아서 디스코드로 알린다 — 다운로드 실패 요약과 같은 패턴
    (건마다 알리면 스팸이 되니 실행 끝날 때 요약 1건). 본문은 build_archive_issue_message."""
    await discord_notify.send_webhook_notification(session, build_archive_issue_message(conflicts, failures))


async def run_archive_job() -> None:
    if _archive_job_lock.locked():
        job_status.log_line("archive", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _archive_job_lock:
        settings = get_settings()
        cancel.reset()  # 지난 중단 요청을 이어받지 않는다
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
            async with read_numbers_provider_for(settings, source="정기 아카이빙") as provider:  # 뷰어 서버가 설정돼 있을 때만 — 뷰어에서 읽지 않은 회차와 읽음 상태를 알 수 없는 작품은 옮기지 않는다
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
            if cancel.is_requested():
                job_status.log_line("archive", f"사용자 요청으로 중단했습니다 (이미 옮긴 {moved + pending_moved}개 파일은 그대로, 남은 파일은 다음 실행에서 이어서 옮깁니다)")
            viewer_scan.note_library_changed(moved + pending_moved, source="정기 아카이빙")  # 파일을 옮겼으면 뷰어 서버가 다시 훑게 한다(연동이 켜져 있을 때만)
            _cleanup_old_archive_history()

            if conflicts or failures:
                async with aiohttp.ClientSession() as session:
                    await _notify_archive_issues(session, conflicts, failures)

            job_status.finish("archive", success=True)
        except Exception as e:
            log.error("아카이빙 잡 중 예외: %s", e)
            job_status.log_line("archive", f"오류: {e}")
            job_status.finish("archive", success=False)
