"""파일 옮기기 — 로컬/rclone 목적지 정하기, 충돌 정책, 회차 파일 이동, 메타 파일 처리.
"""

import logging
import os
import shutil
from pathlib import Path, PurePosixPath

from app import rclone_client, repository
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao

log = logging.getLogger(__name__)

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


def _move_episode_file(
    dest_type: str, src: Path, dest_filename: str | None, policy: str,
    rclone_config_path: str, remote: str, rclone_dest_path: str, local_dest_dir: Path | None,
) -> tuple[str | None, bool]:
    """dest_type에 따라 로컬/rclone 이동 함수 중 맞는 쪽으로 그대로 위임한다.
    _archive_title이 로컬/rclone 분기마다 완전히 같은 반복문을 두 번 쓰지 않도록
    뽑아낸 것 — 실제 이동 규칙(충돌 정책 등)은 각 함수에 그대로 있고, 여기선
    아무 로직도 새로 추가하지 않는다(순수 위임)."""
    if dest_type == "rclone":
        return _move_file_to_rclone_with_conflict_policy(rclone_config_path, src, remote, rclone_dest_path, policy, dest_filename)
    return move_file_with_conflict_policy(src, local_dest_dir, policy, dest_filename)


def _title_folder(download_root: str, title_name: str, kakao: bool) -> Path:
    """다운로드 폴더 아래 작품 폴더 — 폴더 이름 규칙(금지문자 치환)이 플랫폼마다 다르다."""
    return Path(download_root) / (remove_forbidden_str_kakao(title_name) if kakao else remove_forbidden_str(title_name))


def _local_archive_path(archive_root: str, rel_path: str) -> Path:
    """ARCHIVE_ROOT 기준 상대경로를 실제 경로로 바꾸되, 루트 밖으로 못 나가게 검증한다."""
    root = Path(archive_root).resolve()
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError("경로가 아카이브 루트 밖입니다.")
    return target


def _derive_folder_display_name(source_dest_type: str, source_path: str) -> str:
    if source_dest_type == "rclone":
        _remote, path = _parse_rclone_target(source_path)
        return PurePosixPath(path).name or source_path
    return Path(source_path).name or source_path
