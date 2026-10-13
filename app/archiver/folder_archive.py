"""폴더 대상 아카이빙과 일괄 이동 — 폴더 스캔, 표지/메타 옮기기, 복사/이동.
"""

import logging
import os
import shutil
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from app import kakao_cover, rclone_client, repository
from app.zipper import _LEADING_DIGITS_RE

from app.archiver.naming import (
    _list_episode_files_sorted,
    render_archive_filename,
)
from app.archiver.params import FolderMove, ProgressReport
from app.archiver.settings import (
    _get_effective_filename_template,
    get_conflict_policy,
)
from app.archiver.transfer import (
    _cleanup_empty_dirs,
    _derive_folder_display_name,
    _find_metadata_files,
    _local_archive_path,
    _parse_rclone_target,
)

log = logging.getLogger(__name__)

def _bulk_move_collect_source_files(
    kind: str, archive_root: str, rclone_config_path: str, path_value: str
) -> tuple[list[str], object]:
    """원본 위치(로컬/원격) 이하의 모든 '파일'을 상대경로로 모아서 반환한다.
    폴더 자체는 옮기는 대상이 아니다 — 아카이빙 시스템은 항상 파일 단위로 다룬다.
    두 번째 반환값은 실제 이동 시 필요한 컨텍스트: 로컬이면 원본 루트 Path,
    원격이면 (remote, base_path) 튜플."""
    if kind == "local":
        root = _local_archive_path(archive_root, path_value)
        if not root.is_dir():
            raise ValueError("원본 폴더가 존재하지 않습니다.")
        try:
            rel_files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        except OSError as e:
            raise ValueError(f"원본 폴더 목록을 읽을 수 없습니다 (마운트가 불안정할 수 있습니다): {e}") from e
        return rel_files, root

    remote, base_path = _parse_rclone_target(path_value)
    if not remote:
        raise ValueError("원본 원격 정보가 올바르지 않습니다.")
    try:
        rel_files = rclone_client.list_files_recursive(rclone_config_path, remote, base_path)
    except rclone_client.RcloneError as e:
        raise ValueError(f"원본 원격 폴더 목록을 읽을 수 없습니다: {e}") from e
    return rel_files, (remote, base_path)


def _bulk_move_dest_exists(kind: str, dest_ctx, rel_path: str, rclone_config_path: str) -> bool:
    if kind == "local":
        return (dest_ctx / rel_path).exists()
    remote, base_path = dest_ctx
    rel = PurePosixPath(rel_path)
    check_dir = f"{base_path}/{rel.parent}" if str(rel.parent) != "." else base_path
    return rclone_client.file_exists(rclone_config_path, remote, check_dir, rel.name)


def _bulk_move_resolve_final_rel(
    policy: str, dest_kind: str, dest_ctx, rel_path: str, rclone_config_path: str
) -> str | None:
    """목적지에 이미 같은 파일이 있을 때 정책에 따라 최종 상대경로를 정한다.
    None이면 건너뛴다는 뜻. 파일명만 바뀌고 상위 폴더 구조는 그대로 유지한다."""
    if not _bulk_move_dest_exists(dest_kind, dest_ctx, rel_path, rclone_config_path):
        return rel_path
    if policy == "skip":
        return None
    if policy == "overwrite":
        return rel_path

    # rename: 파일명 뒤에 (2), (3)... 붙여서 안 겹치는 이름을 찾는다
    rel = PurePosixPath(rel_path)
    stem, suffix = rel.stem, rel.suffix
    counter = 2
    while True:
        candidate = str(rel.parent / f"{stem} ({counter}){suffix}") if str(rel.parent) != "." else f"{stem} ({counter}){suffix}"
        if not _bulk_move_dest_exists(dest_kind, dest_ctx, candidate, rclone_config_path):
            return candidate
        counter += 1


def _bulk_move_single_file(
    *, src_kind: str, src_ctx, dest_kind: str, dest_ctx,
    rel_path: str, final_rel: str, rclone_config_path: str,
) -> None:
    """파일 하나를 원본에서 목적지(final_rel 경로)로 옮긴다.
    로컬-로컬은 shutil로, 원격이 하나라도 끼면 rclone moveto로 처리한다."""
    if src_kind == "local" and dest_kind == "local":
        dest_abs = dest_ctx / final_rel
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_ctx / rel_path), str(dest_abs))
        return

    if src_kind == "local":
        src_spec = str(src_ctx / rel_path)
    else:
        remote, base_path = src_ctx
        src_spec = f"{remote}:{base_path}/{rel_path}" if base_path else f"{remote}:{rel_path}"

    if dest_kind == "local":
        dest_abs = dest_ctx / final_rel
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        dest_spec = str(dest_abs)
    else:
        remote, base_path = dest_ctx
        dest_spec = f"{remote}:{base_path}/{final_rel}" if base_path else f"{remote}:{final_rel}"

    rclone_client.moveto(rclone_config_path, src_spec, dest_spec)


def _generic_file_spec(kind: str, ctx, name: str) -> str:
    """local이면 절대경로 문자열, rclone이면 'remote:path/name' 문자열을 만든다.
    rclone_client의 copyto/moveto는 이 문자열이 로컬 경로인지 remote:path인지
    (콜론 유무로) 알아서 구분하므로, 이 문자열 하나로 로컬/원격 어느 쪽이든
    똑같이 넘길 수 있다."""
    if kind == "local":
        return str(ctx / name)
    remote, path = ctx
    return f"{remote}:{path}/{name}" if path else f"{remote}:{name}"


def _generic_copy_file(src_kind: str, src_ctx, dest_kind: str, dest_ctx, name: str, rclone_config_path: str) -> None:
    """metadata 파일(info.xml/cover)을 원본은 남긴 채 복사한다 — 로컬/원격 어느 조합이든 동작."""
    src_spec = _generic_file_spec(src_kind, src_ctx, name)
    dest_spec = _generic_file_spec(dest_kind, dest_ctx, name)
    if src_kind == "local" and dest_kind == "local":
        Path(dest_spec).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_spec, dest_spec)
        return
    if dest_kind == "local":
        Path(dest_spec).parent.mkdir(parents=True, exist_ok=True)
    rclone_client.copyto(rclone_config_path, src_spec, dest_spec)


def _generic_move_file(src_kind: str, src_ctx, dest_kind: str, dest_ctx, name: str, rclone_config_path: str) -> None:
    """metadata 파일을 원본에서 지우면서 옮긴다(완결 전체이동용)."""
    _bulk_move_single_file(
        src_kind=src_kind, src_ctx=src_ctx, dest_kind=dest_kind, dest_ctx=dest_ctx,
        rel_path=name, final_rel=name, rclone_config_path=rclone_config_path,
    )


def _scan_folder_source(target, rclone_config_path: str, archive_root: str, display_name: str, failure_log: "list | None"):
    """폴더 대상의 원본을 훑어서 (종류, 컨텍스트, [(번호, 회차 zip 이름)], [정보 파일 이름])을 돌려준다. 원본이 없거나 원격 정보/목록이 잘못이면
    실패를 기록하고 None."""
    src_kind = target.source_dest_type
    if src_kind == "local":
        src_ctx = _local_archive_path(archive_root, target.source_path)
        if not src_ctx.is_dir():
            if failure_log is not None:
                failure_log.append((display_name, "-", "원본 폴더가 존재하지 않습니다."))
            return None
        rel_files = [(n, p.name) for n, p in _list_episode_files_sorted(src_ctx)]
        return src_kind, src_ctx, rel_files, [p.name for p in _find_metadata_files(src_ctx)]

    remote, path = _parse_rclone_target(target.source_path)
    if not remote:
        if failure_log is not None:
            failure_log.append((display_name, "-", "원본 원격 정보가 올바르지 않습니다."))
        return None
    try:
        names = rclone_client.list_top_level_files(rclone_config_path, remote, path)
    except rclone_client.RcloneError as e:
        if failure_log is not None:
            failure_log.append((display_name, "-", f"원본 원격 폴더 목록 조회 실패: {e}"))
        return None
    rel_files = []
    for name in names:
        if not name.endswith(".zip"):
            continue
        m = _LEADING_DIGITS_RE.match(name)
        if m:
            rel_files.append((int(m.group(1)), name))
    rel_files.sort(key=lambda t: t[0])
    return src_kind, (remote, path), rel_files, [n for n in names if n == "info.xml" or n.startswith("cover.")]


def _refresh_folder_cover(src_kind: str, src_ctx, meta_names: list[str], rclone_config_path: str, display_name: str, progress_callback):
    """완결 처리일 때 카카오페이지 공식 표지로 새로 받는다. 로컬 원본은 폴더 안에서 바로 새 cover.jpg로 바꾸고(정보 파일 목록을 다시 읽음),
    원격 원본은 info.xml만 가볍게 읽어 판별한 뒤 받은 표지를 임시 로컬 파일로만 만들어 둔다. (임시 표지 경로 또는 None, 정보 파일 목록)을
    돌려준다. 실패해도 이동 자체는 계속한다."""
    try:
        if src_kind == "local":
            if kakao_cover.refresh_kakao_cover_if_applicable(src_ctx):
                if progress_callback:
                    progress_callback(f"[{display_name}] 카카오 표지 갱신함")
                meta_names = [p.name for p in _find_metadata_files(src_ctx)]
            return None, meta_names
        if "info.xml" in meta_names:
            remote, path = src_ctx
            xml_text = rclone_client.read_small_text_file(rclone_config_path, remote, path, "info.xml")
            series_id = kakao_cover.parse_kakao_web_url(_extract_web_url_from_xml_text(xml_text) or "")
            if series_id:
                regenerated = _download_kakao_cover_to_temp_file(series_id)
                if regenerated and progress_callback:
                    progress_callback(f"[{display_name}] 카카오 표지 갱신함")
                return regenerated, meta_names
    except Exception as e:
        log.warning("카카오 표지 갱신 중 예외 (무시하고 계속) (%s): %s", display_name, e)
    return None, meta_names


def _prepare_folder_dest(target, rclone_config_path: str, archive_root: str, display_name: str, failure_log: "list | None"):
    """목적지(폴더/원격 경로)를 만들고 (종류, 컨텍스트)를 돌려준다. 원격 정보가 잘못이면 실패를 기록하고 None."""
    if target.dest_type == "local":
        dest_ctx = _local_archive_path(archive_root, target.dest_base_path)
        dest_ctx.mkdir(parents=True, exist_ok=True)
        return target.dest_type, dest_ctx
    remote, path = _parse_rclone_target(target.dest_base_path)
    if not remote:
        if failure_log is not None:
            failure_log.append((display_name, "-", "목적지 원격 정보가 올바르지 않습니다."))
        return None
    if path:
        rclone_client.create_folder(rclone_config_path, remote, path)
    return target.dest_type, (remote, path)


def _move_folder_files(rel_files: list, move: FolderMove, report: ProgressReport) -> int:
    """회차 zip들을 하나씩 옮기고(파일명 템플릿, 충돌 정책, 이력) 옮긴 개수를 돌려준다. 한 파일이 실패해도 나머지는 계속한다."""
    moved = 0
    for _num, name in rel_files:
        try:
            dest_filename = None
            if move.template.strip():
                if move.src_kind == "local":
                    rendered = render_archive_filename(move.template, name, move.display_name, [], zip_path_for_page_count=move.src_ctx / name)
                else:
                    # 원격 원본: zip을 못 열어서, 파일명 자체에 페이지수가 있는 구조(카카오식)일 때만
                    # 적용된다 — {page_count}가 있는데 zip을 열어야 하는 구조(네이버식)면 원본 유지.
                    rendered = render_archive_filename(move.template, name, move.display_name, [])
                if rendered is not None:
                    dest_filename = rendered
            final_name = dest_filename or name
            final_rel = _bulk_move_resolve_final_rel(move.policy, move.dest_kind, move.dest_ctx, final_name, move.rclone_config_path)
            had_conflict = final_rel is None or final_rel != final_name
            if final_rel is None:
                report.progress(f"[{move.display_name}] 건너뜀(이미 존재): {name}")
                report.conflict(move.display_name, name, move.policy)
                continue
            if had_conflict:
                report.conflict(move.display_name, name, move.policy)
            _bulk_move_single_file(
                src_kind=move.src_kind, src_ctx=move.src_ctx, dest_kind=move.dest_kind, dest_ctx=move.dest_ctx,
                rel_path=name, final_rel=final_rel, rclone_config_path=move.rclone_config_path,
            )
            moved += 1
            repository.add_archive_history(move.target.title_id, move.display_name, final_rel, move.trigger_type)
            report.progress(f"[{move.display_name}] 이동 완료: {final_rel}")
        except Exception as e:
            log.error("폴더 대상 아카이빙 이동 실패 (%s/%s): %s", move.display_name, name, e)
            report.progress(f"[{move.display_name}] 이동 실패: {name} — {e}")
            report.failure(move.display_name, name, str(e))
    return moved


def _transfer_folder_metadata(
    src_kind: str, src_ctx, dest_kind: str, dest_ctx, meta_names: list[str], regenerated_local_cover: "str | None",
    keep_last: bool, rclone_config_path: str, display_name: str,
) -> None:
    """info.xml/cover를 옮긴다 — 주기/수동은 복사(커버는 목적지에 이미 있으면 건너뜀), 완결 처리는 이동. 새로 받은 임시 표지가 있으면 목적지
    cover.jpg로 올리고 임시 파일은 항상 지운다. 어느 쪽이든 실패는 무시하고 계속한다(이동 자체를 막지 않는다)."""
    for name in meta_names:
        try:
            if keep_last:
                if name.startswith("cover.") and _bulk_move_dest_exists(dest_kind, dest_ctx, name, rclone_config_path):
                    continue
                _generic_copy_file(src_kind, src_ctx, dest_kind, dest_ctx, name, rclone_config_path)
            else:
                _generic_move_file(src_kind, src_ctx, dest_kind, dest_ctx, name, rclone_config_path)
        except Exception as e:
            log.warning("폴더 대상 메타데이터 이동 실패 (%s/%s, 무시하고 계속): %s", display_name, name, e)

    if regenerated_local_cover is not None:
        try:
            dest_spec = _generic_file_spec(dest_kind, dest_ctx, "cover.jpg")
            if dest_kind == "local":
                Path(dest_spec).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(regenerated_local_cover, dest_spec)
            else:
                rclone_client.copyto(rclone_config_path, str(regenerated_local_cover), dest_spec)
        except Exception as e:
            log.warning("카카오 표지 업로드 실패 (%s, 무시하고 계속): %s", display_name, e)
        finally:
            Path(regenerated_local_cover).unlink(missing_ok=True)


def _cleanup_source_folder(src_kind: str, src_ctx, rclone_config_path: str) -> None:
    """옮기고 나서 원본 쪽에 파일이 하나도 안 남은 빈 폴더를 정리한다."""
    if src_kind == "local":
        _cleanup_empty_dirs(src_ctx)
    else:
        remote, path = src_ctx
        rclone_client.rmdirs_if_empty(rclone_config_path, remote, path)


def _archive_folder_target(
    target, policy: str, rclone_config_path: str, archive_root: str, keep_last: bool,
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
) -> int:
    """폴더-폴더 대상(웹툰 레코드가 없는 카카오웹툰 등) 아카이빙. _archive_title과
    같은 규칙(마지막 파일 보존/완결 전체이동, 파일명 템플릿, 카카오 표지 갱신,
    이력 기록)을 쓰되, 다운로드 루트의 title_id 대신 등록된 원본 폴더를 그대로 쓴다.

    원본이 로컬이면 회차 zip 파일명 템플릿({page_count} 포함)을 전부 지원한다.
    원본이 rclone이면 zip 내용을 굳이 내려받지 않기 위해 {page_count}가 들어간
    템플릿은 적용하지 않고(파일마다 안전하게 원본 이름 유지) 나머지 이동/이름변경은
    그대로 서버측으로 처리한다 — info.xml만 가볍게 읽어서 카카오 표지 갱신은 그대로 지원한다."""
    display_name = target.display_name or _derive_folder_display_name(target.source_dest_type, target.source_path)
    trigger_type = "manual_finish" if not keep_last else "manual"
    template = _get_effective_filename_template(target.filename_template_preset_id)

    scanned = _scan_folder_source(target, rclone_config_path, archive_root, display_name, failure_log)
    if scanned is None:
        return 0
    src_kind, src_ctx, rel_files, meta_names = scanned

    regenerated_local_cover = None
    if not keep_last:  # 카카오 표지 갱신은 완결 처리일 때만
        regenerated_local_cover, meta_names = _refresh_folder_cover(src_kind, src_ctx, meta_names, rclone_config_path, display_name, progress_callback)

    if keep_last and len(rel_files) > 0:
        rel_files = rel_files[:-1]  # 마지막(가장 큰 번호)은 보존

    if not rel_files and not meta_names and regenerated_local_cover is None:
        return 0

    dest = _prepare_folder_dest(target, rclone_config_path, archive_root, display_name, failure_log)
    if dest is None:
        return 0
    dest_kind, dest_ctx = dest

    moved = _move_folder_files(
        rel_files,
        FolderMove(
            target=target, src_kind=src_kind, src_ctx=src_ctx, dest_kind=dest_kind, dest_ctx=dest_ctx, policy=policy, template=template,
            display_name=display_name, trigger_type=trigger_type, rclone_config_path=rclone_config_path,
        ),
        ProgressReport(progress_callback, conflict_log, failure_log),
    )
    _transfer_folder_metadata(src_kind, src_ctx, dest_kind, dest_ctx, meta_names, regenerated_local_cover, keep_last, rclone_config_path, display_name)
    if not keep_last:
        _cleanup_source_folder(src_kind, src_ctx, rclone_config_path)
    return moved


def _extract_web_url_from_xml_text(xml_text: str | None) -> str | None:
    if not xml_text:
        return None
    try:
        web = ElementTree.fromstring(xml_text).find("Web")
    except ElementTree.ParseError:
        return None  # 깨진 info.xml은 "알 수 없음" — 다른 오류(프로그램 실수)까지 숨기지 않으려고 이 예외만 받는다
    return (web.text or "").strip() if web is not None else None


def _download_kakao_cover_to_temp_file(series_id: str) -> str | None:
    """rclone 원본용 — info.xml만 가볍게 읽어서 카카오페이지로 판별된 뒤, 공식 표지는 카카오에서
    직접 받는다(원본 폴더 자체를 내려받지 않아도 됨). 받은 표지를 임시 로컬 파일로 저장하고
    그 경로를 반환한다(실패 시 None)."""
    import tempfile

    jpeg_bytes = kakao_cover.fetch_official_cover_bytes(series_id)
    if jpeg_bytes is None:
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="kakao_cover_")
    os.close(fd)
    Path(tmp_path).write_bytes(jpeg_bytes)
    return tmp_path


def _bulk_prepare_cover(
    regenerate: bool, source_type: str, source_local_root: str, source_path: str, rclone_config_path: str, progress_callback
) -> "str | None":
    """카카오 표지 갱신 옵션. 로컬 원본은 목록을 모으기 전에 그 자리에서 바로 새 cover.jpg로 바꿔 두면(원본 폴더 안의 파일을 실제로
    바꾸는 것이므로) 이후 목록 조회에 자연스럽게 새 파일로 잡힌다. 원격 원본은 폴더 전체를 내려받을 수 없으니 info.xml만 가볍게 읽어
    판별한 뒤 받은 표지를 임시 파일로만 만들어 두고(그 경로를 돌려준다), 이동 반복에서 원본 cover.*를 이 파일로 교체해서 옮긴다.
    꺼져 있거나 카카오 작품이 아니거나 실패하면 None — 실패해도 이동은 계속한다."""
    if not regenerate:
        return None
    temp_path = None
    try:
        if source_type == "local":
            local_src_dir = _local_archive_path(source_local_root, source_path)
            if kakao_cover.refresh_kakao_cover_if_applicable(local_src_dir) and progress_callback:
                progress_callback("카카오 표지 갱신함")
        else:
            remote, path = _parse_rclone_target(source_path)
            xml_text = rclone_client.read_small_text_file(rclone_config_path, remote, path, "info.xml")
            series_id = kakao_cover.parse_kakao_web_url(_extract_web_url_from_xml_text(xml_text) or "")
            if series_id:
                temp_path = _download_kakao_cover_to_temp_file(series_id)
                if temp_path and progress_callback:
                    progress_callback("카카오 표지 갱신함")
    except Exception as e:
        log.warning("일괄 이동 중 카카오 표지 갱신 실패 (무시하고 계속): %s", e)
    return temp_path


def _bulk_prepare_dest(dest_type: str, dest_local_root: str, dest_path: str, rclone_config_path: str):
    """목적지 컨텍스트(로컬이면 폴더 Path, 원격이면 (remote, base_path))를 만들어 돌려준다. 원격 정보가 잘못이면 ValueError."""
    if dest_type == "local":
        dest_ctx = _local_archive_path(dest_local_root, dest_path)
        dest_ctx.mkdir(parents=True, exist_ok=True)
        return dest_ctx
    remote, base_path = _parse_rclone_target(dest_path)
    if not remote:
        raise ValueError("목적지 원격 정보가 올바르지 않습니다.")
    if base_path:
        rclone_client.create_folder(rclone_config_path, remote, base_path)
    return remote, base_path


def _bulk_place_refreshed_cover(
    temp_cover: str, rel: PurePosixPath, policy: str, dest_type: str, dest_ctx, src_ctx, rclone_config_path: str
) -> "str | None":
    """받아 둔 새 표지로 원본 cover.*를 대체해서 목적지의 cover.jpg로 둔다(원본 cover는 지우고 임시 파일도 정리). 목적지에 이미 있고
    정책이 건너뛰기면 None — 그러면 호출부가 원본 cover를 일반 파일처럼 처리한다. 둔 최종 상대경로를 돌려준다."""
    dest_cover_rel = str(rel.parent / "cover.jpg") if str(rel.parent) != "." else "cover.jpg"
    final_rel = _bulk_move_resolve_final_rel(policy, dest_type, dest_ctx, dest_cover_rel, rclone_config_path)
    if final_rel is None:
        return None
    dest_spec = _generic_file_spec(dest_type, dest_ctx, final_rel)
    if dest_type == "local":
        Path(dest_spec).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(temp_cover, dest_spec)
    else:
        rclone_client.copyto(rclone_config_path, temp_cover, dest_spec)
    remote_src, base_src = src_ctx
    del_dir = f"{base_src}/{rel.parent}" if str(rel.parent) != "." else base_src
    rclone_client.delete_file(rclone_config_path, remote_src, del_dir, rel.name)
    Path(temp_cover).unlink(missing_ok=True)
    return final_rel


def _bulk_desired_rel_path(rel_path: str, filename_template: str, source_type: str, source_path: str, src_ctx) -> str:
    """파일명 템플릿을 적용한 목적지 상대경로. {title}은 하위 폴더 안 파일이면 그 바로 위 폴더명, 최상위 파일이면 원본 폴더 이름이다.
    템플릿이 없거나 파일명 구조를 인식 못 하면 원래 상대경로 그대로(그 파일만 — 일부 인식 안 되는 파일이 있다고 전체가 실패하지 않는다)."""
    if not filename_template.strip():
        return rel_path
    rel = PurePosixPath(rel_path)
    title_candidate = rel.parent.name if str(rel.parent) != "." else _derive_folder_display_name(source_type, source_path)
    zip_path_for_page_count = (src_ctx / rel_path) if source_type == "local" else None
    rendered = render_archive_filename(filename_template, rel.name, title_candidate, [], zip_path_for_page_count=zip_path_for_page_count)
    if rendered is None:
        return rel_path
    return str(rel.parent / rendered) if str(rel.parent) != "." else rendered


def bulk_move_folder(
    source_local_root: str, dest_local_root: str, rclone_config_path: str,
    source_type: str, source_path: str,
    dest_type: str, dest_path: str,
    progress_callback=None, filename_template: str = "",
    regenerate_kakao_cover: bool = False,
) -> int:
    """1회성 폴더→폴더 전체 이동 (아카이빙 대상 지정 규칙과 무관, 백업 정리용).
    로컬-로컬/로컬-원격/원격-로컬/원격-원격 네 조합을 전부 지원한다.
    항상 '파일' 단위로 옮기며(폴더 자체를 통째로 옮기지 않음), 옮긴 뒤 원본 쪽에
    파일이 하나도 안 남은 빈 폴더는 정리한다.

    source_local_root/dest_local_root: source_type/dest_type이 "local"일 때 그
    경로가 기준으로 삼는 실제 루트 폴더(ARCHIVE_ROOT 또는 DOWNLOAD_ROOT일 수 있음
    — 호출부가 정해서 넘긴다. 이미 완결됐는데 구독 안 해서 자동 아카이빙 대상엔
    못 올리는 웹툰처럼, 다운로드 폴더에 있는 걸 그대로 보관 폴더로 옮기고 싶을 때
    DOWNLOAD_ROOT를 원본으로 쓸 수 있게 하기 위함). rclone 쪽이면 안 쓰인다.

    filename_template(선택): 비워두면(기본) 파일명을 안 건드리고 그대로 옮긴다.
    지정하면 파일명 변경 프리셋과 동일한 방식(render_archive_filename)으로 각
    파일의 이름을 바꿔서 옮긴다 — {title}은 그 파일이 하위 폴더 안에 있으면 그
    바로 위 폴더명을, 최상위에 바로 있으면 원본 폴더 자체의 이름을 쓴다. 파일명
    구조를 인식 못 하면(카카오식/네이버식 둘 다 아니면) 그 파일만 원본 이름
    그대로 옮긴다 — 일부 인식 안 되는 파일이 있다고 전체가 실패하지 않는다.

    regenerate_kakao_cover(선택, 기본 꺼짐): 켜면 원본 폴더가 카카오웹툰이면
    (info.xml의 <Web> 태그로 판단) 표지를 카카오페이지 공식 표지로 새로 받아서 옮긴다
    — 아카이빙 대상(완결 처리)과 같은 로직이지만, 일괄 이동은 "완결/주기" 구분이
    없는 1회성 이동이라 매번 강제로 하지 않고 옵션으로만 켤 수 있게 했다.

    progress_callback(선택): 파일 하나 처리할 때마다 사람이 읽을 진행 메시지
    문자열 하나를 넘겨서 호출한다. archiver.py는 이 메시지를 어디에 기록할지
    (화면 표시용 job_status 등) 전혀 모른다 — 호출부(routes.py)가 원하는 대로
    쓰도록 콜백으로만 분리해서, 이 모듈이 웹/잡 상태 계층에 의존하지 않게 한다."""
    policy = get_conflict_policy()
    kakao_cover_temp_path = _bulk_prepare_cover(
        regenerate_kakao_cover, source_type, source_local_root, source_path, rclone_config_path, progress_callback
    )

    rel_files, src_ctx = _bulk_move_collect_source_files(source_type, source_local_root, rclone_config_path, source_path)
    total = len(rel_files)
    if progress_callback:
        progress_callback(f"이동할 파일 {total}개 확인, 시작합니다")

    # 이력에는 파일 하나마다 한 줄씩 남긴다 — 주기/수동/완결 이동과 같은 단위로 남겨야, "이력 → 아카이빙 이력"에서 어떤 이동
    # 방식이든 항상 같은 수준의 기록을 볼 수 있다(예전엔 일괄 이동만 작업 전체에 한 줄만 남겨서 단위가 달랐음).
    batch_label = f"{source_path} → {dest_path}"
    dest_ctx = _bulk_prepare_dest(dest_type, dest_local_root, dest_path, rclone_config_path)

    moved = 0
    for index, rel_path in enumerate(rel_files, start=1):
        try:
            rel = PurePosixPath(rel_path)
            if kakao_cover_temp_path and rel.name.lower().startswith("cover."):
                placed = _bulk_place_refreshed_cover(kakao_cover_temp_path, rel, policy, dest_type, dest_ctx, src_ctx, rclone_config_path)
                if placed is not None:
                    kakao_cover_temp_path = None  # 표지는 한 번만 대체한다(임시 파일은 위에서 지움)
                    moved += 1
                    repository.add_archive_history("-", batch_label, placed, "bulk_move")
                    if progress_callback:
                        progress_callback(f"[{index}/{total}] 이동 완료(표지 갱신): {placed}")
                    continue

            desired_rel_path = _bulk_desired_rel_path(rel_path, filename_template, source_type, source_path, src_ctx)
            final_rel = _bulk_move_resolve_final_rel(policy, dest_type, dest_ctx, desired_rel_path, rclone_config_path)
            if final_rel is None:
                log.info("일괄 이동 건너뜀 (이미 존재): %s", rel_path)
                if progress_callback:
                    progress_callback(f"[{index}/{total}] 건너뜀(이미 존재): {rel_path}")
                continue
            _bulk_move_single_file(
                src_kind=source_type, src_ctx=src_ctx, dest_kind=dest_type, dest_ctx=dest_ctx,
                rel_path=rel_path, final_rel=final_rel, rclone_config_path=rclone_config_path,
            )
            moved += 1
            repository.add_archive_history("-", batch_label, final_rel, "bulk_move")
            if progress_callback:
                progress_callback(f"[{index}/{total}] 이동 완료: {final_rel}")
        except Exception as e:
            log.error("일괄 이동 중 개별 파일 실패, 건너뜀 (%s): %s", rel_path, e)
            if progress_callback:
                progress_callback(f"[{index}/{total}] 실패(건너뜀): {rel_path} — {e}")

    if kakao_cover_temp_path:  # 원본에 cover 파일이 없어 쓰이지 않은 임시 표지
        Path(kakao_cover_temp_path).unlink(missing_ok=True)

    if progress_callback:
        progress_callback("원본 쪽 빈 폴더 정리 중")
    _cleanup_source_folder(source_type, src_ctx, rclone_config_path)
    return moved
