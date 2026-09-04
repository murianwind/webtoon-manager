"""
웹툰 아카이빙: 다운로드 폴더(A)에 쌓인 회차 zip을 사용자가 지정한 별도 폴더(B, ARCHIVE_ROOT
기준)로 옮긴다. 세 가지 트리거가 있다 — 주기적 이동(마지막 파일 보존), 수동 이동(동일 규칙),
완결 구독해제 시 자동 전체이동(마지막 파일 예외 없음). 셋 다 목적지 계산(resolve_archive_dest)과
실제 이동(move_file_with_conflict_policy)이라는 같은 두 함수를 거치므로 로직이 갈라지지 않는다.

핵심 설계 결정(전부 사용자와 논의 확정):
- "마지막 파일"은 파일명 맨 앞 번호 기준 — zipper.py의 _LEADING_DIGITS_RE를 그대로 재사용해서
  번호 파싱 로직이 두 곳에서 어긋나지 않게 한다.
- 목적지 base_path는 여러 웹툰이 공유할 수 있는 "그릇" 폴더 — 실제 저장 위치는
  {base_path}/{원본 폴더명} 서브폴더가 되는데, 이건 그 base_path를 "지정해서" 쓰는
  활성(enabled) 웹툰이 2개 이상일 때만 그렇다. 1개면 base_path 바로 밑에 쌓인다.
  완결 자동이동의 기본 경로(archive_default_base_path)는 항상 여러 웹툰을 받는 공용
  그릇이 목적이므로 예외 없이 항상 서브폴더를 만든다.
- 이미 파일이 하나라도 있는 폴더를 목적지로 지정하려 하면 경고를 준다(선택 API에서
  검증) — 나중에 다른 웹툰이 같은 폴더를 고르면서 파일이 뒤섞이는 걸 막기 위한
  주의 장치일 뿐, 강제로 막지는 않는다(예전엔 여기서 막았었지만, 사용자 요청으로
  경고 후 진행 가능하도록 완화됨).
"""

import logging
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from app import rclone_client, repository
from app.file_utils import remove_forbidden_str
from app.zipper import _LEADING_DIGITS_RE, _clean_name

log = logging.getLogger(__name__)

_CONFLICT_POLICY_SETTING_KEY = "archive_conflict_policy"
_DEFAULT_BASE_PATH_SETTING_KEY = "archive_default_base_path"
_DEFAULT_DEST_TYPE_SETTING_KEY = "archive_default_dest_type"
_ON_FINISH_UNSUBSCRIBE_SETTING_KEY = "archive_on_finish_unsubscribe"
_FILENAME_TEMPLATE_SETTING_KEY = "archive_filename_template"


def _parse_zip_filename_for_template(stem: str, title_name: str) -> tuple[str, str] | None:
    """zip 파일명(확장자 제외)이 '{일련번호} {제목} {부제목}' 구조라고 가정하고
    (회차 번호 문자열 그대로, 부제목)을 뽑는다. 회차 번호는 원래 문자열 그대로
    반환한다(0채움 자릿수 등 원본 표기를 그대로 유지하기 위함 — 정수로 변환하지 않음).
    제목이 파일명 구조와 안 맞으면(수동으로 넣은 파일 등) None을 반환해서, 호출부가
    파일명 템플릿을 적용하지 않고 원본 파일명을 그대로 쓰게 한다."""
    match = _LEADING_DIGITS_RE.match(stem)
    if not match:
        return None
    episode_no = match.group(1)
    rest = stem[match.end():].lstrip()

    # zip 파일명 속 제목은 다운로드 시(remove_forbidden_str) + 압축 시(_clean_name)
    # 두 번 치환을 거친 상태이므로, 비교 대상 제목도 같은 치환을 거쳐야 맞아떨어진다.
    cleaned_title = _clean_name(remove_forbidden_str(title_name))
    for candidate in (title_name, cleaned_title):
        if rest.startswith(candidate):
            subtitle = rest[len(candidate):].strip()
            if subtitle:
                return episode_no, subtitle
    return None


def _list_episode_files_sorted(title_dir: Path) -> list[tuple[int, Path]]:
    """제목 폴더 바로 밑의 zip 파일들을, 파일명 맨 앞 번호 기준으로 오름차순 정렬해서 반환한다."""
    if not title_dir.is_dir():
        return []
    results = []
    try:
        entries = list(title_dir.iterdir())
    except OSError as e:
        log.error("다운로드 폴더 목록 조회 실패 (%s): %s", title_dir, e)
        return []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".zip":
            continue
        match = _LEADING_DIGITS_RE.match(entry.name)
        if match:
            results.append((int(match.group(1)), entry))
    results.sort(key=lambda pair: pair[0])
    return results


def _find_metadata_files(title_dir: Path) -> list[Path]:
    """제목 폴더 바로 밑의 info.xml / cover.* 파일을 찾는다 (여러 확장자 대응)."""
    if not title_dir.is_dir():
        return []
    found = []
    info_xml = title_dir / "info.xml"
    if info_xml.is_file():
        found.append(info_xml)
    found.extend(p for p in title_dir.glob("cover.*") if p.is_file())
    return found


def _archive_metadata_files_local(metadata_files: list[Path], dest_dir: Path, *, keep_last: bool) -> None:
    """info.xml/커버를 로컬 목적지로 옮긴다.
    keep_last=True(주기/수동): 원본은 남기고 복사. info.xml은 작은 텍스트 파일이라
    매번 새로 복사하는 비용이 거의 없어서 항상 덮어쓴다(오래된 정보가 보관 폴더에
    그대로 굳어있는 걸 막기 위함) — 반면 커버 이미지는 용량이 있고 거의 안 바뀌므로
    목적지에 이미 있으면 건너뛴다.
    keep_last=False(완결 전체이동): 원본을 옮기고, 목적지에 있어도 무조건 덮어쓴다
    (zip과 달리 충돌 정책의 영향을 받지 않음 — 사용자와 논의 확정)."""
    for src in metadata_files:
        dest_path = dest_dir / src.name
        try:
            if keep_last:
                if src.name.startswith("cover.") and dest_path.exists():
                    continue
                shutil.copy2(src, dest_path)
            else:
                shutil.move(str(src), str(dest_path))
        except OSError as e:
            log.error("메타데이터 파일 이동 실패 (%s): %s", src, e)


def _archive_metadata_files_rclone(
    rclone_config_path: str, metadata_files: list[Path], remote: str, dest_path: str, *, keep_last: bool
) -> None:
    for src in metadata_files:
        dest_spec = f"{remote}:{dest_path}/{src.name}" if dest_path else f"{remote}:{src.name}"
        try:
            if keep_last:
                if src.name.startswith("cover.") and rclone_client.file_exists(rclone_config_path, remote, dest_path, src.name):
                    continue
                rclone_client.copyto(rclone_config_path, str(src), dest_spec)
            else:
                rclone_client.moveto(rclone_config_path, str(src), dest_spec)
        except rclone_client.RcloneError as e:
            log.error("메타데이터 파일 원격 이동 실패 (%s): %s", src, e)


def _cleanup_empty_dirs(root: Path) -> None:
    """root 이하(자신 포함)에서 파일이 하나도 안 남은 폴더를 하위부터 지운다.
    파일이 하나라도 있으면 그 폴더(와 상위)는 절대 지우지 않는다 — 안전이 최우선."""
    if not root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError as e:
            log.warning("빈 폴더 정리 실패 (무시하고 계속): %s: %s", d, e)


def is_folder_selectable_as_dest(archive_root: str, base_path: str) -> bool:
    """이 폴더가 비어있는지 확인한다 (True=비어있음). 반환값은 호출부(routes.py)가
    "경고를 보여줄지"를 판단하는 데만 쓰인다 — 파일이 있다고 강제로 막지는 않는다
    (예전엔 여기서 등록 자체를 막았지만, 사용자 요청으로 경고 후 진행 가능하게 완화됨).
    rclone 마운트처럼 일반적이지 않은 폴더는 목록 조회 자체가 예외를 던질 수 있어서
    (실제로 겪은 사례) 안전하게 "선택 불가(파일 있음)"로 처리한다 — 뭔가 있는지 확신
    못 하면 비어있다고 잘못 판단해서 파일이 섞이는 것보다는, 경고를 띄우는 쪽이 안전하다."""
    dest = Path(archive_root) / base_path
    if not dest.exists():
        return True
    try:
        return not any(dest.iterdir())
    except OSError as e:
        log.warning("폴더 내용 확인 실패, 안전하게 선택 불가 처리 (%s): %s", dest, e)
        return False


def _should_use_subfolder(base_path: str, dest_type: str, *, force_subfolder: bool) -> bool:
    """이 base_path를 지정해서 쓰는 활성 웹툰이 2개 이상이면(또는 완결 자동이동의
    기본 경로라 항상 여러 웹툰을 받는 공용 그릇이면) 웹툰별 서브폴더를 만든다.
    로컬/rclone 둘 다 이 판단 기준을 공유한다 — 예전엔 이 로직이 두 곳에 거의
    똑같이 복사돼 있었다."""
    if force_subfolder:
        return True
    sharing_count = repository.count_archive_targets_with_base_path(base_path, dest_type)
    return sharing_count > 1


def resolve_archive_dest(archive_root: str, title_name: str, base_path: str, *, force_subfolder: bool) -> Path:
    """실제 저장 위치를 계산하고 폴더를 만들어서 반환한다."""
    use_subfolder = _should_use_subfolder(base_path, "local", force_subfolder=force_subfolder)

    if use_subfolder:
        dest = Path(archive_root) / base_path / remove_forbidden_str(title_name)
    else:
        dest = Path(archive_root) / base_path
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def move_file_with_conflict_policy(src: Path, dest_dir: Path, policy: str, dest_filename: str | None = None) -> tuple[str | None, bool]:
    """src를 dest_dir로 옮긴다. dest_filename을 주면 원본 이름 대신 그 이름으로 저장한다
    (파일명 템플릿 적용용). 반환값은 (실제로 저장된 파일명(성공 시) 또는 None(건너뛴 경우),
    목적지에 이미 같은 이름 파일이 있어서 정책이 실제로 작동했는지 여부).
    policy: 'overwrite' | 'skip' | 'rename'"""
    name = dest_filename or src.name
    dest_path = dest_dir / name
    had_conflict = dest_path.exists()
    if had_conflict:
        if policy == "skip":
            log.info("아카이빙 건너뜀 (이미 존재): %s", dest_path)
            return None, True
        elif policy == "rename":
            stem, suffix = Path(name).stem, Path(name).suffix
            counter = 2
            while dest_path.exists():
                dest_path = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1
        # overwrite는 dest_path 그대로 두고 shutil.move가 덮어쓰게 둔다

    shutil.move(str(src), str(dest_path))
    return dest_path.name, had_conflict


def get_conflict_policy() -> str:
    return repository.get_setting(_CONFLICT_POLICY_SETTING_KEY) or "skip"


def get_default_base_path() -> str | None:
    return repository.get_setting(_DEFAULT_BASE_PATH_SETTING_KEY)


def get_default_dest_type() -> str:
    return repository.get_setting(_DEFAULT_DEST_TYPE_SETTING_KEY) or "local"


def get_filename_template() -> str:
    """빈 문자열이면 파일명 템플릿 미사용(원본 zip 파일명 그대로 이동)을 뜻한다."""
    return repository.get_setting(_FILENAME_TEMPLATE_SETTING_KEY) or ""


def _count_zip_pages(zip_path: Path) -> int | None:
    """zip 안에 든 이미지(페이지) 개수를 실제로 세서 반환한다. zip을 못 열면(손상 등)
    None을 반환한다 — 이 경우 {page_count} 토큰이 있는 템플릿은 적용하지 않는다."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return sum(1 for n in zf.namelist() if not n.endswith("/"))
    except (zipfile.BadZipFile, OSError) as e:
        log.warning("zip 페이지 수 계산 실패 (%s): %s", zip_path, e)
        return None


def render_archive_filename(
    template: str, src: Path, title_name: str, writer_names: list[str]
) -> str | None:
    """템플릿에 맞춰 최종 파일명(확장자 포함)을 만든다. 템플릿이 비어있거나, 파일명
    구조를 못 알아보거나(_parse_zip_filename_for_template), 템플릿에 {page_count}가
    있는데 zip을 못 열면 None을 반환해서 호출부가 원본 파일명을 그대로 쓰게 한다."""
    if not template.strip():
        return None
    parsed = _parse_zip_filename_for_template(src.stem, title_name)
    if parsed is None:
        return None
    episode_no, subtitle = parsed

    rendered = template
    if "{page_count}" in rendered:
        page_count = _count_zip_pages(src)
        if page_count is None:
            return None
        rendered = rendered.replace("{page_count}", str(page_count))
    rendered = rendered.replace("{title}", title_name)
    rendered = rendered.replace("{episode_no}", episode_no)
    rendered = rendered.replace("{subtitle}", subtitle)
    rendered = rendered.replace("{author}", ", ".join(writer_names))

    rendered = remove_forbidden_str(rendered)
    if not rendered:
        return None
    return rendered + src.suffix


def preview_filename_for_title(download_root: str, title_name: str, template: str, writer_names: list[str]) -> dict:
    """설정 화면의 미리보기용 — 실제 다운로드 폴더에 있는 회차 zip 파일 하나를 골라서,
    템플릿을 적용하면 실제로 어떤 이름이 되는지 보여준다. 가장 번호가 큰(최근) 파일을
    보여준다 — 사용자가 알아보기 쉬운 최신 회차일 가능성이 높아서."""
    title_dir = Path(download_root) / remove_forbidden_str(title_name)
    files = _list_episode_files_sorted(title_dir)
    if not files:
        return {"original_filename": None, "rendered_filename": None, "message": "다운로드 폴더에 zip 파일이 없습니다."}
    _num, src = files[-1]
    if not template.strip():
        return {"original_filename": src.name, "rendered_filename": None, "message": "템플릿이 비어있어 원본 파일명 그대로 이동됩니다."}
    rendered = render_archive_filename(template, src, title_name, writer_names)
    if rendered is None:
        return {
            "original_filename": src.name,
            "rendered_filename": None,
            "message": "이 파일명 구조를 인식하지 못해 원본 파일명 그대로 이동됩니다.",
        }
    return {"original_filename": src.name, "rendered_filename": rendered, "message": "정상적으로 변환됩니다."}


def is_finish_unsubscribe_archiving_enabled() -> bool:
    return repository.get_setting(_ON_FINISH_UNSUBSCRIBE_SETTING_KEY) == "1"


def _parse_rclone_target(base_path: str) -> tuple[str, str]:
    """'remote:path/to/folder' -> ('remote', 'path/to/folder')."""
    remote, _, path = base_path.partition(":")
    return remote, path


def is_folder_selectable_as_dest_rclone(rclone_config_path: str, base_path: str) -> bool:
    remote, path = _parse_rclone_target(base_path)
    if not remote:
        return False
    return rclone_client.is_folder_empty(rclone_config_path, remote, path)


def _resolve_archive_dest_rclone(rclone_config_path: str, title_name: str, base_path: str, *, force_subfolder: bool) -> str:
    remote, path = _parse_rclone_target(base_path)
    use_subfolder = _should_use_subfolder(base_path, "rclone", force_subfolder=force_subfolder)

    if use_subfolder:
        folder_name = remove_forbidden_str(title_name)
        dest_path = f"{path}/{folder_name}" if path else folder_name
        rclone_client.create_folder(rclone_config_path, remote, dest_path)
    else:
        dest_path = path
    return f"{remote}:{dest_path}"


def _move_file_to_rclone_with_conflict_policy(
    rclone_config_path: str, src: Path, remote: str, dest_path: str, policy: str, dest_filename: str | None = None
) -> tuple[str | None, bool]:
    dest_name = dest_filename or src.name
    had_conflict = rclone_client.file_exists(rclone_config_path, remote, dest_path, dest_name)
    if had_conflict:
        if policy == "skip":
            log.info("아카이빙 건너뜀 (원격에 이미 존재): %s:%s/%s", remote, dest_path, dest_name)
            return None, True
        elif policy == "rename":
            stem = Path(dest_name).stem
            suffix = Path(dest_name).suffix
            counter = 2
            while rclone_client.file_exists(rclone_config_path, remote, dest_path, dest_name):
                dest_name = f"{stem} ({counter}){suffix}"
                counter += 1
        # overwrite: 그대로 진행, rclone moveto가 덮어씀

    try:
        rclone_client.move_file_to_remote(rclone_config_path, str(src), remote, dest_path, dest_name)
    except rclone_client.RcloneError as e:
        if not src.exists():
            # moveto는 목적지 복사가 성공적으로 끝난 걸 확인한 뒤에만 원본을 지운다.
            # 그러니 시간초과 에러가 나도 로컬 원본이 이미 없어졌다면, 실제로는
            # 업로드까지 다 끝나고 rclone이 마지막 정리 단계에서 응답이 늦어졌을
            # 가능성이 높다 — 이걸 그냥 "실패"로 처리하면 파일이 어디로도 기록 안
            # 남고 사라진 것처럼 보이는 문제가 실제로 있었다. 성공으로 간주하되
            # 불확실하다는 걸 로그에 명확히 남긴다.
            log.warning(
                "rclone 응답 시간초과(%s)가 났지만 로컬 원본이 이미 사라짐 — 실제로는 "
                "업로드가 성공했을 가능성이 높습니다. 원격(%s:%s/%s)에서 직접 확인해보세요.",
                e, remote, dest_path, dest_name,
            )
        else:
            raise
    return dest_name, had_conflict


def _archive_title(
    archive_root: str, download_root: str, title_id: str, title_name: str,
    base_path: str, policy: str, trigger_type: str, keep_last: bool,
    dest_type: str = "local", rclone_config_path: str = "",
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
    writer_names: list[str] | None = None,
) -> int:
    """실제로 파일들을 옮기고 이력을 남긴다. 반환값은 옮긴 개수.
    dest_type이 'rclone'이면 로컬 shutil 대신 rclone CLI로 처리한다(Windows 마운트를
    거치지 않아서, Docker Desktop이 WinFsp 가상 드라이브를 못 읽는 문제를 우회함).

    progress_callback(선택): 파일 하나 처리할 때마다 진행 메시지 문자열로 호출한다
    (일괄 이동과 동일한 방식) — 이 함수는 그 메시지를 화면에 어떻게 보여줄지 모른다.
    conflict_log/failure_log(선택): 지정하면, 이번 파일에서 이름 충돌이 실제로
    있었거나(정책이 작동함) 이동 자체가 실패했을 때 (title_name, file_name, 상세)를
    그 리스트에 추가한다 — 호출부(scheduler.py)가 실행이 다 끝난 뒤 모아서 디스코드로
    알릴 때 쓴다."""
    if dest_type == "rclone" and not (rclone_config_path and Path(rclone_config_path).is_file()):
        log.error("rclone 목적지인데 RCLONE_CONFIG_PATH가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
        return 0
    if dest_type == "local" and not archive_root:
        log.error("로컬 목적지인데 ARCHIVE_ROOT가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
        return 0

    title_dir = Path(download_root) / remove_forbidden_str(title_name)
    files = _list_episode_files_sorted(title_dir)
    if keep_last and len(files) > 0:
        files = files[:-1]  # 마지막(가장 큰 번호)은 보존

    metadata_files = _find_metadata_files(title_dir)
    if not files and not metadata_files:
        return 0

    force_subfolder = base_path == get_default_base_path()
    moved = 0
    template = get_filename_template()

    if dest_type == "rclone":
        dest_target = _resolve_archive_dest_rclone(rclone_config_path, title_name, base_path, force_subfolder=force_subfolder)
        remote, dest_path = _parse_rclone_target(dest_target)
        for _num, src in files:
            try:
                dest_filename = render_archive_filename(template, src, title_name, writer_names or []) if template else None
                saved_name, had_conflict = _move_file_to_rclone_with_conflict_policy(
                    rclone_config_path, src, remote, dest_path, policy, dest_filename
                )
                if had_conflict and conflict_log is not None:
                    conflict_log.append((title_name, src.name, policy))
                if saved_name is not None:
                    repository.add_archive_history(title_id, title_name, saved_name, trigger_type)
                    moved += 1
                    if progress_callback:
                        progress_callback(f"[{title_name}] 이동 완료: {saved_name}")
                elif progress_callback:
                    progress_callback(f"[{title_name}] 건너뜀(이미 존재): {src.name}")
            except Exception as e:
                log.error("rclone 아카이빙 이동 실패 (%s): %s", src, e)
                if progress_callback:
                    progress_callback(f"[{title_name}] 이동 실패: {src.name} — {e}")
                if failure_log is not None:
                    failure_log.append((title_name, src.name, str(e)))
        if metadata_files:
            _archive_metadata_files_rclone(rclone_config_path, metadata_files, remote, dest_path, keep_last=keep_last)
    else:
        dest_dir = resolve_archive_dest(archive_root, title_name, base_path, force_subfolder=force_subfolder)
        for _num, src in files:
            try:
                dest_filename = render_archive_filename(template, src, title_name, writer_names or []) if template else None
                saved_name, had_conflict = move_file_with_conflict_policy(src, dest_dir, policy, dest_filename)
                if had_conflict and conflict_log is not None:
                    conflict_log.append((title_name, src.name, policy))
                if saved_name is not None:
                    repository.add_archive_history(title_id, title_name, saved_name, trigger_type)
                    moved += 1
                    if progress_callback:
                        progress_callback(f"[{title_name}] 이동 완료: {saved_name}")
                elif progress_callback:
                    progress_callback(f"[{title_name}] 건너뜀(이미 존재): {src.name}")
            except Exception as e:
                log.error("아카이빙 이동 실패 (%s): %s", src, e)
                if progress_callback:
                    progress_callback(f"[{title_name}] 이동 실패: {src.name} — {e}")
                if failure_log is not None:
                    failure_log.append((title_name, src.name, str(e)))
        if metadata_files:
            _archive_metadata_files_local(metadata_files, dest_dir, keep_last=keep_last)
    return moved


def run_periodic_archive(
    archive_root: str, download_root: str, rclone_config_path: str = "",
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
) -> int:
    """지정된(enabled) 웹툰 전부, 마지막 파일 보존하며 이동. 반환값은 전체 이동 개수."""
    policy = get_conflict_policy()
    total = 0
    for target in repository.list_archive_targets():
        if not target.enabled:
            continue
        wt = repository.get(target.title_id)
        if wt is None:
            continue
        total += _archive_title(
            archive_root, download_root, target.title_id, wt.title,
            target.dest_base_path, policy, "periodic", keep_last=True,
            dest_type=target.dest_type, rclone_config_path=rclone_config_path,
            progress_callback=progress_callback, conflict_log=conflict_log, failure_log=failure_log,
            writer_names=wt.writer_names,
        )
    return total


def manual_archive_now(
    archive_root: str, download_root: str, title_ids: list[str], rclone_config_path: str = "",
    progress_callback=None, full_move: bool = False,
) -> int:
    """수동 실행 — 기본은 지정된 것과 동일 규칙(마지막 파일 보존).
    full_move=True면 완결 자동이동과 동일하게 마지막 파일까지 전부 옮기고, 다 옮긴 뒤
    다운로드 폴더가 비면 그 폴더도 정리한다 — "완결 처리했는데 자동이동 설정을 안 켜놨던"
    웹툰을 그때그때 수동으로 완전히 정리하고 싶을 때 쓰는 용도라, 연재 중인 웹툰에도
    (사용자 책임 하에) 쓸 수 있게 굳이 is_finished 여부를 확인하지 않는다 — 대신 화면
    쪽에서 체크박스 + 재확인으로 실수를 막는다."""
    policy = get_conflict_policy()
    trigger_type = "manual_finish" if full_move else "manual"
    total = 0
    for title_id in title_ids:
        target = repository.get_archive_target(title_id)
        if target is None:
            continue
        wt = repository.get(title_id)
        if wt is None:
            continue
        total += _archive_title(
            archive_root, download_root, title_id, wt.title,
            target.dest_base_path, policy, trigger_type, keep_last=not full_move,
            dest_type=target.dest_type, rclone_config_path=rclone_config_path,
            progress_callback=progress_callback,
            writer_names=wt.writer_names,
        )
        if full_move:
            title_dir = Path(download_root) / remove_forbidden_str(wt.title)
            _cleanup_empty_dirs(title_dir)
            if progress_callback:
                progress_callback(f"[{wt.title}] 완결 처리 — 다운로드 폴더 정리 확인")
    return total


def process_pending_finish_archives(
    archive_root: str, download_root: str, rclone_config_path: str = "",
    progress_callback=None, conflict_log: list | None = None, failure_log: list | None = None,
) -> int:
    """완결 구독해제로 대기열에 쌓인 웹툰들을 처리한다 — 아카이빙 주기가 돌 때
    run_periodic_archive와 함께 호출된다(즉시 실행 대신 같은 주기에 묶임)."""
    if not is_finish_unsubscribe_archiving_enabled():
        return 0

    total = 0
    for title_id in repository.list_pending_finish_archive():
        wt = repository.get(title_id)
        if wt is None:
            repository.remove_pending_finish_archive(title_id)
            continue

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
        total += _archive_title(
            archive_root, download_root, title_id, wt.title,
            base_path, policy, "finish_unsubscribe", keep_last=False,
            dest_type=dest_type, rclone_config_path=rclone_config_path,
            progress_callback=progress_callback, conflict_log=conflict_log, failure_log=failure_log,
            writer_names=wt.writer_names,
        )
        # 완결 전체이동은 마지막 파일도 예외 없이 옮기므로, 다 옮기고 나면 다운로드
        # 쪽 웹툰 폴더엔 아무 것도 안 남는 게 정상이다 — 파일이 하나라도 남아있으면
        # (예: 이동 중 일부 실패) 안전하게 그대로 두고, 완전히 비었을 때만 지운다.
        title_dir = Path(download_root) / remove_forbidden_str(wt.title)
        _cleanup_empty_dirs(title_dir)
        repository.remove_pending_finish_archive(title_id)
    return total


def _local_archive_path(archive_root: str, rel_path: str) -> Path:
    """ARCHIVE_ROOT 기준 상대경로를 실제 경로로 바꾸되, 루트 밖으로 못 나가게 검증한다."""
    root = Path(archive_root).resolve()
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError("경로가 아카이브 루트 밖입니다.")
    return target


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
            raise ValueError(f"원본 폴더 목록을 읽을 수 없습니다 (마운트가 불안정할 수 있습니다): {e}")
        return rel_files, root

    remote, base_path = _parse_rclone_target(path_value)
    if not remote:
        raise ValueError("원본 원격 정보가 올바르지 않습니다.")
    try:
        rel_files = rclone_client.list_files_recursive(rclone_config_path, remote, base_path)
    except rclone_client.RcloneError as e:
        raise ValueError(f"원본 원격 폴더 목록을 읽을 수 없습니다: {e}")
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


def bulk_move_folder(
    archive_root: str, rclone_config_path: str,
    source_type: str, source_path: str,
    dest_type: str, dest_path: str,
    progress_callback=None,
) -> int:
    """1회성 폴더→폴더 전체 이동 (아카이빙 대상 지정 규칙과 무관, 백업 정리용).
    로컬-로컬/로컬-원격/원격-로컬/원격-원격 네 조합을 전부 지원한다.
    항상 '파일' 단위로 옮기며(폴더 자체를 통째로 옮기지 않음), 옮긴 뒤 원본 쪽에
    파일이 하나도 안 남은 빈 폴더는 정리한다.

    progress_callback(선택): 파일 하나 처리할 때마다 사람이 읽을 진행 메시지
    문자열 하나를 넘겨서 호출한다. archiver.py는 이 메시지를 어디에 기록할지
    (화면 표시용 job_status 등) 전혀 모른다 — 호출부(routes.py)가 원하는 대로
    쓰도록 콜백으로만 분리해서, 이 모듈이 웹/잡 상태 계층에 의존하지 않게 한다."""
    policy = get_conflict_policy()
    rel_files, src_ctx = _bulk_move_collect_source_files(source_type, archive_root, rclone_config_path, source_path)
    total = len(rel_files)
    if progress_callback:
        progress_callback(f"이동할 파일 {total}개 확인, 시작합니다")

    # 이력에는 파일 하나마다 한 줄씩 남긴다 — 주기/수동/완결 이동과 같은 단위로
    # 남겨야, "이력 → 아카이빙 이력"에서 어떤 이동 방식이든 항상 같은 수준의
    # 기록을 볼 수 있다(예전엔 일괄 이동만 작업 전체에 한 줄만 남겨서 단위가 달랐음).
    batch_label = f"{source_path} → {dest_path}"

    if dest_type == "local":
        dest_ctx = _local_archive_path(archive_root, dest_path)
        dest_ctx.mkdir(parents=True, exist_ok=True)
    else:
        remote, base_path = _parse_rclone_target(dest_path)
        if not remote:
            raise ValueError("목적지 원격 정보가 올바르지 않습니다.")
        if base_path:
            rclone_client.create_folder(rclone_config_path, remote, base_path)
        dest_ctx = (remote, base_path)

    moved = 0
    for index, rel_path in enumerate(rel_files, start=1):
        try:
            final_rel = _bulk_move_resolve_final_rel(policy, dest_type, dest_ctx, rel_path, rclone_config_path)
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
                progress_callback(f"[{index}/{total}] 이동 완료: {rel_path}")
        except Exception as e:
            log.error("일괄 이동 중 개별 파일 실패, 건너뜀 (%s): %s", rel_path, e)
            if progress_callback:
                progress_callback(f"[{index}/{total}] 실패(건너뜀): {rel_path} — {e}")

    if progress_callback:
        progress_callback("원본 쪽 빈 폴더 정리 중")
    if source_type == "local":
        _cleanup_empty_dirs(src_ctx)
    else:
        remote, base_path = src_ctx
        rclone_client.rmdirs_if_empty(rclone_config_path, remote, base_path)

    return moved
