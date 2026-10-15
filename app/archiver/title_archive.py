"""작품(구독) 아카이빙 — 작품별 정기/수동/완결 후 아카이빙.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from app import kakao_cover, repository

from app.archiver.folder_archive import (
    _archive_folder_target,
)
from app.archiver.naming import (
    _list_episode_files_sorted,
    render_archive_filename,
)
from app.archiver.params import ProgressReport, TitleArchiveJob, TitleDestination
from app.archiver.settings import (
    _get_effective_filename_template,
    get_conflict_policy,
    get_default_base_path,
    get_default_dest_type,
    is_finish_unsubscribe_archiving_enabled,
)
from app.archiver.transfer import (
    _archive_metadata_files_local,
    _archive_metadata_files_rclone,
    _cleanup_empty_dirs,
    _find_metadata_files,
    _move_episode_file,
    _parse_rclone_target,
    _resolve_archive_dest_rclone,
    _title_folder,
    resolve_archive_dest,
)

log = logging.getLogger(__name__)

KAKAO_TARGET_PREFIX = "kakao_"  # 카카오페이지 웹툰 대상의 id는 "kakao_<시리즈 번호>" — 네이버 title_id와 번호가 겹치지 않게


@dataclass
class WebtoonSource:
    title: str
    writer_names: list[str]
    kakao: bool  # True면 카카오페이지 다운로드 폴더/카카오 폴더 이름 규칙을 쓴다


def resolve_webtoon_source(title_id: str) -> "WebtoonSource | None":
    """웹툰 유형 아카이빙 대상의 id로 작품 정보를 찾는다(네이버 title_id 또는 "kakao_<번호>"). 없으면 None."""
    if title_id.startswith(KAKAO_TARGET_PREFIX):
        try:
            record = repository.get_kakao_webtoon(int(title_id[len(KAKAO_TARGET_PREFIX):]))
        except ValueError:
            return None
        if record is None:
            return None
        # {author}는 네이버처럼 "글" 작가 — 작품 정보에서 저장해 둔 글 작가를 쓰고, 아직 저장 전이면(예전에 구독만 하고 받은 적이
        # 없는 작품) 목록이 주는 작가 이름(역할 구분 없음)으로 대신한다
        names = record["writer_names"] or [n.strip() for n in (record["author_summary"] or "").split(",") if n.strip()]
        return WebtoonSource(title=record["title"], writer_names=names, kakao=True)
    wt = repository.get(title_id)
    return None if wt is None else WebtoonSource(title=wt.title, writer_names=wt.writer_names, kakao=False)


def queue_finish_archive_if_applicable(title_id: str, is_finished: bool) -> bool:
    """완결 작품을 구독해제했고 "완결 구독해제 시 자동 이동"이 켜져 있으면 이동 대기열에 넣는다(실제 이동은 다음 아카이빙 주기 때).
    네이버(title_id)와 카카오페이지("kakao_<번호>") 모두 같은 규칙이다. 넣었으면 True."""
    if is_finished and is_finish_unsubscribe_archiving_enabled():
        repository.add_pending_finish_archive(title_id)
        return True
    return False


def _dest_settings_missing(dest_type: str, archive_root: str, rclone_config_path: str, title_id: str) -> bool:
    """목적지에 필요한 설정이 없으면 로그를 남기고 True — 그 작품은 건너뛴다."""
    if dest_type == "rclone" and not (rclone_config_path and Path(rclone_config_path).is_file()):
        log.error("rclone 목적지인데 RCLONE_CONFIG_PATH가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
        return True
    if dest_type == "local" and not archive_root:
        log.error("로컬 목적지인데 ARCHIVE_ROOT가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
        return True
    return False


def _refresh_kakao_cover_for_finish(title_dir: Path, title_name: str, progress_callback) -> None:
    """완결 처리(수동 "완결 처리로 이동"이든 완결 자동이동이든)일 때만 — 이 폴더가 카카오페이지 작품이면(info.xml의 <Web> 태그로 판단)
    표지를 카카오페이지의 공식 표지로 새로 받아 교체한다. 실패해도 아카이빙 자체는 계속 진행한다."""
    try:
        if kakao_cover.refresh_kakao_cover_if_applicable(title_dir) and progress_callback:
            progress_callback(f"[{title_name}] 카카오 표지 갱신함")
    except Exception as e:
        log.warning("카카오 표지 갱신 중 예외 (무시하고 계속) (%s): %s", title_dir, e)


def _resolve_title_dest(dest_type: str, archive_root: str, rclone_config_path: str, title_name: str, base_path: str) -> tuple[str, str, "Path | None"]:
    """(rclone 원격 이름, rclone 목적지 경로, 로컬 목적지 폴더) — 해당하지 않는 쪽은 빈 값. 목적지 폴더를 만들고 돌려준다."""
    force_subfolder = base_path == get_default_base_path()
    if dest_type == "rclone":
        dest_target = _resolve_archive_dest_rclone(rclone_config_path, title_name, base_path, force_subfolder=force_subfolder)
        remote, rclone_dest_path = _parse_rclone_target(dest_target)
        return remote, rclone_dest_path, None
    return "", "", resolve_archive_dest(archive_root, title_name, base_path, force_subfolder=force_subfolder)


def _move_title_files(files: list, dest: TitleDestination, report: ProgressReport) -> int:
    """회차 zip들을 하나씩 옮기고(파일명 템플릿 적용, 충돌 정책, 이력) 옮긴 개수를 돌려준다. 한 파일이 실패해도 나머지는 계속한다."""
    moved = 0
    for _num, src in files:
        try:
            dest_filename = (
                render_archive_filename(dest.template, src.name, dest.title_name, dest.writer_names, zip_path_for_page_count=src) if dest.template else None
            )
            saved_name, had_conflict = _move_episode_file(
                dest.dest_type, src, dest_filename, dest.policy, dest.rclone_config_path, dest.remote, dest.rclone_dest_path, dest.local_dest_dir
            )
            if had_conflict:
                report.conflict(dest.title_name, src.name, dest.policy)
            if saved_name is not None:
                repository.add_archive_history(dest.title_id, dest.title_name, saved_name, dest.trigger_type)
                moved += 1
                report.progress(f"[{dest.title_name}] 이동 완료: {saved_name}")
            else:
                report.progress(f"[{dest.title_name}] 건너뜀(이미 존재): {src.name}")
        except Exception as e:
            log.error("아카이빙 이동 실패 (%s): %s", src, e)
            report.progress(f"[{dest.title_name}] 이동 실패: {src.name} — {e}")
            report.failure(dest.title_name, src.name, str(e))
    return moved


def _archive_title(job: TitleArchiveJob, report: ProgressReport | None = None) -> int:
    """실제로 파일들을 옮기고 이력을 남긴다. 반환값은 옮긴 개수.
    job.dest_type이 'rclone'이면 로컬 shutil 대신 rclone CLI로 처리한다(Windows 마운트를
    거치지 않아서, Docker Desktop이 WinFsp 가상 드라이브를 못 읽는 문제를 우회함).

    report(선택): 파일 하나 처리할 때마다 진행 메시지를 알리고(일괄 이동과 동일한 방식 — 이 함수는
    그 메시지를 화면에 어떻게 보여줄지 모른다), 지정한 충돌/실패 목록에는 이번 파일에서 이름 충돌이
    실제로 있었거나(정책이 작동함) 이동 자체가 실패했을 때 (title_name, file_name, 상세)를 추가한다 —
    호출부(scheduler)가 실행이 다 끝난 뒤 모아서 디스코드로 알릴 때 쓴다."""
    report = report or ProgressReport()
    if _dest_settings_missing(job.dest_type, job.archive_root, job.rclone_config_path, job.title_id):
        return 0

    title_dir = _title_folder(job.download_root, job.title_name, job.kakao)
    files = _list_episode_files_sorted(title_dir)
    if job.keep_last and len(files) > 0:
        files = files[:-1]  # 마지막(가장 큰 번호)은 보존

    if not job.keep_last:
        _refresh_kakao_cover_for_finish(title_dir, job.title_name, report.progress_callback)

    metadata_files = _find_metadata_files(title_dir)
    if not files and not metadata_files:
        return 0

    template = _get_effective_filename_template(job.filename_template_preset_id)
    remote, rclone_dest_path, local_dest_dir = _resolve_title_dest(job.dest_type, job.archive_root, job.rclone_config_path, job.title_name, job.base_path)
    dest = TitleDestination(
        title_id=job.title_id, title_name=job.title_name, dest_type=job.dest_type, policy=job.policy, trigger_type=job.trigger_type,
        template=template, writer_names=job.writer_names or [], rclone_config_path=job.rclone_config_path,
        remote=remote, rclone_dest_path=rclone_dest_path, local_dest_dir=local_dest_dir,
    )
    moved = _move_title_files(files, dest, report)

    if metadata_files:
        if job.dest_type == "rclone":
            _archive_metadata_files_rclone(job.rclone_config_path, metadata_files, remote, rclone_dest_path, keep_last=job.keep_last)
        else:
            _archive_metadata_files_local(metadata_files, local_dest_dir, keep_last=job.keep_last)
    return moved


def run_periodic_archive(
    archive_root: str, download_root: str, rclone_config_path: str = "",
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
    kakao_download_root: str | None = None,
) -> int:
    """지정된(enabled) 웹툰/폴더 대상 전부, 마지막 파일 보존하며 이동. 반환값은 전체 이동 개수."""
    policy = get_conflict_policy()
    total = 0
    for target in repository.list_archive_targets():
        if not target.enabled:
            continue
        if target.source_type == "folder":
            total += _archive_folder_target(
                target, policy, rclone_config_path, archive_root, keep_last=True,
                progress_callback=progress_callback, conflict_log=conflict_log, failure_log=failure_log,
            )
            continue
        source = resolve_webtoon_source(target.title_id)
        if source is None:
            continue
        total += _archive_title(
            TitleArchiveJob(
                archive_root=archive_root, download_root=(kakao_download_root or download_root) if source.kakao else download_root,
                title_id=target.title_id, title_name=source.title, base_path=target.dest_base_path, policy=policy, trigger_type="periodic", keep_last=True,
                dest_type=target.dest_type, rclone_config_path=rclone_config_path, writer_names=source.writer_names,
                filename_template_preset_id=target.filename_template_preset_id, kakao=source.kakao,
            ),
            ProgressReport(progress_callback, conflict_log, failure_log),
        )
    return total


def manual_archive_now(
    archive_root: str, download_root: str, title_ids: list[str], rclone_config_path: str = "",
    progress_callback=None, full_move: bool = False, kakao_download_root: str | None = None,
) -> int:
    """수동 실행 — 기본은 지정된 것과 동일 규칙(마지막 파일 보존).
    full_move=True면 완결 자동이동과 동일하게 마지막 파일까지 전부 옮기고, 다 옮긴 뒤
    다운로드 폴더가 비면 그 폴더도 정리한다 — "완결 처리했는데 자동이동 설정을 안 켜놨던"
    웹툰을 그때그때 수동으로 완전히 정리하고 싶을 때 쓰는 용도라, 연재 중인 웹툰에도
    (사용자 책임 하에) 쓸 수 있게 굳이 is_finished 여부를 확인하지 않는다 — 대신 화면
    쪽에서 체크박스 + 재확인으로 실수를 막는다. title_ids에는 웹툰 title_id와
    폴더 대상의 합성 id(folder_로 시작)가 섞여서 올 수 있다."""
    policy = get_conflict_policy()
    trigger_type = "manual_finish" if full_move else "manual"
    total = 0
    for title_id in title_ids:
        target = repository.get_archive_target(title_id)
        if target is None:
            continue
        if target.source_type == "folder":
            folder_failures: list = []
            total += _archive_folder_target(
                target, policy, rclone_config_path, archive_root, keep_last=not full_move,
                progress_callback=progress_callback, failure_log=folder_failures,
            )
            if full_move:
                _delete_target_after_full_move(title_id, target.display_name or title_id, not folder_failures, ProgressReport(progress_callback))
            continue
        source = resolve_webtoon_source(title_id)
        if source is None:
            continue
        root = (kakao_download_root or download_root) if source.kakao else download_root
        own_failures: list = []
        report = ProgressReport(progress_callback, None, own_failures)
        total += _archive_title(
            TitleArchiveJob(
                archive_root=archive_root, download_root=root, title_id=title_id, title_name=source.title, base_path=target.dest_base_path,
                policy=policy, trigger_type=trigger_type, keep_last=not full_move, dest_type=target.dest_type, rclone_config_path=rclone_config_path,
                writer_names=source.writer_names, filename_template_preset_id=target.filename_template_preset_id, kakao=source.kakao,
            ),
            report,
        )
        if full_move:
            title_dir = _title_folder(root, source.title, source.kakao)
            _cleanup_empty_dirs(title_dir)
            report.progress(f"[{source.title}] 완결 처리 — 다운로드 폴더 정리 확인")
            _delete_target_after_full_move(title_id, source.title, not own_failures and not title_dir.exists(), report)
    return total


def _delete_target_after_full_move(title_id: str, title_name: str, completed: bool, report: ProgressReport) -> None:
    """완결 전체 이동이 문제없이 끝났으면(실패가 없고 원본 쪽이 정리됨) 그 대상의 아카이빙 설정을 삭제한다 — 더 옮길 게 없는 설정이 대상 목록과
    아카이빙 수동 실행 목록에 남지 않게. 일부 실패했거나 파일이 남았거나 보관 위치가 모자라 못 옮겼으면 설정을 남겨서 다시 시도할 수 있게 한다."""
    if not completed or repository.get_archive_target(title_id) is None:
        return
    repository.delete_archive_target(title_id)
    report.progress(f"[{title_name}] 완결 처리 완료 — 아카이빙 대상에서 삭제했습니다")


def process_pending_finish_archives(
    archive_root: str, download_root: str, rclone_config_path: str = "",
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
    kakao_download_root: str | None = None,
) -> int:
    """완결 구독해제로 대기열에 쌓인 웹툰들을 처리한다 — 아카이빙 주기가 돌 때
    run_periodic_archive와 함께 호출된다(즉시 실행 대신 같은 주기에 묶임)."""
    if not is_finish_unsubscribe_archiving_enabled():
        return 0

    total = 0
    for title_id in repository.list_pending_finish_archive():
        source = resolve_webtoon_source(title_id)
        if source is None:
            repository.remove_pending_finish_archive(title_id)
            continue
        root = (kakao_download_root or download_root) if source.kakao else download_root

        target = repository.get_archive_target(title_id)
        if target is not None and target.enabled:
            base_path = target.dest_base_path
            dest_type = target.dest_type
        else:
            base_path = get_default_base_path()
            dest_type = get_default_dest_type()
            if not base_path:
                log.warning("완결 자동이동 기본 경로가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
                continue

        policy = get_conflict_policy()
        own_failures: list = []  # 이번 작품의 이동 실패만 따로 모아서, 문제없이 끝났는지 판단하고 호출부 목록에도 그대로 전달한다
        report = ProgressReport(progress_callback, conflict_log, own_failures)
        total += _archive_title(
            TitleArchiveJob(
                archive_root=archive_root, download_root=root, title_id=title_id, title_name=source.title, base_path=base_path, policy=policy,
                trigger_type="finish_unsubscribe", keep_last=False, dest_type=dest_type, rclone_config_path=rclone_config_path,
                writer_names=source.writer_names, filename_template_preset_id=target.filename_template_preset_id if target else None, kakao=source.kakao,
            ),
            report,
        )
        # 완결 전체이동은 마지막 파일도 예외 없이 옮기므로, 다 옮기고 나면 다운로드
        # 쪽 웹툰 폴더엔 아무 것도 안 남는 게 정상이다 — 파일이 하나라도 남아있으면
        # (예: 이동 중 일부 실패) 안전하게 그대로 두고, 완전히 비었을 때만 지운다.
        title_dir = _title_folder(root, source.title, source.kakao)
        _cleanup_empty_dirs(title_dir)
        if failure_log is not None:
            failure_log.extend(own_failures)
        _delete_target_after_full_move(title_id, source.title, not own_failures and not title_dir.exists(), report)
        repository.remove_pending_finish_archive(title_id)
    return total
