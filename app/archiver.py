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
- 이미 파일이 하나라도 있는 폴더는 새로 목적지로 지정할 수 없다(선택 API에서 검증) —
  나중에 다른 웹툰이 같은 폴더를 고르면서 파일이 뒤섞이는 문제를 원천 차단하기 위함.
"""

import logging
import shutil
from pathlib import Path

from app import rclone_client, repository
from app.file_utils import remove_forbidden_str
from app.zipper import _LEADING_DIGITS_RE

log = logging.getLogger(__name__)

_CONFLICT_POLICY_SETTING_KEY = "archive_conflict_policy"
_DEFAULT_BASE_PATH_SETTING_KEY = "archive_default_base_path"
_DEFAULT_DEST_TYPE_SETTING_KEY = "archive_default_dest_type"
_ON_FINISH_UNSUBSCRIBE_SETTING_KEY = "archive_on_finish_unsubscribe"


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


def is_folder_selectable_as_dest(archive_root: str, base_path: str) -> bool:
    """이미 파일이 하나라도 있으면 새 목적지로 선택 불가 (뒤섞임 방지).
    rclone 마운트처럼 일반적이지 않은 폴더는 목록 조회 자체가 예외를 던질 수 있어서
    (실제로 겪은 사례) 안전하게 "선택 불가"로 처리한다 — 뭔가 있는지 확신 못 하면
    비어있다고 잘못 판단해서 파일이 섞이는 것보다는, 막아두는 쪽이 안전하다."""
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


def move_file_with_conflict_policy(src: Path, dest_dir: Path, policy: str) -> str | None:
    """src를 dest_dir로 옮긴다. 반환값은 실제로 저장된 파일명(성공 시) 또는 None(건너뛴 경우).
    policy: 'overwrite' | 'skip' | 'rename'"""
    dest_path = dest_dir / src.name
    if dest_path.exists():
        if policy == "skip":
            log.info("아카이빙 건너뜀 (이미 존재): %s", dest_path)
            return None
        elif policy == "rename":
            stem, suffix = src.stem, src.suffix
            counter = 2
            while dest_path.exists():
                dest_path = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1
        # overwrite는 dest_path 그대로 두고 shutil.move가 덮어쓰게 둔다

    shutil.move(str(src), str(dest_path))
    return dest_path.name


def get_conflict_policy() -> str:
    return repository.get_setting(_CONFLICT_POLICY_SETTING_KEY) or "skip"


def get_default_base_path() -> str | None:
    return repository.get_setting(_DEFAULT_BASE_PATH_SETTING_KEY)


def get_default_dest_type() -> str:
    return repository.get_setting(_DEFAULT_DEST_TYPE_SETTING_KEY) or "local"


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


def _move_file_to_rclone_with_conflict_policy(rclone_config_path: str, src: Path, remote: str, dest_path: str, policy: str) -> str | None:
    dest_name = src.name
    if rclone_client.file_exists(rclone_config_path, remote, dest_path, dest_name):
        if policy == "skip":
            log.info("아카이빙 건너뜀 (원격에 이미 존재): %s:%s/%s", remote, dest_path, dest_name)
            return None
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
    return dest_name


def _archive_title(
    archive_root: str, download_root: str, title_id: str, title_name: str,
    base_path: str, policy: str, trigger_type: str, keep_last: bool,
    dest_type: str = "local", rclone_config_path: str = "",
) -> int:
    """실제로 파일들을 옮기고 이력을 남긴다. 반환값은 옮긴 개수.
    dest_type이 'rclone'이면 로컬 shutil 대신 rclone CLI로 처리한다(Windows 마운트를
    거치지 않아서, Docker Desktop이 WinFsp 가상 드라이브를 못 읽는 문제를 우회함)."""
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

    if not files:
        return 0

    force_subfolder = base_path == get_default_base_path()
    moved = 0

    if dest_type == "rclone":
        dest_target = _resolve_archive_dest_rclone(rclone_config_path, title_name, base_path, force_subfolder=force_subfolder)
        remote, dest_path = _parse_rclone_target(dest_target)
        for _num, src in files:
            try:
                saved_name = _move_file_to_rclone_with_conflict_policy(rclone_config_path, src, remote, dest_path, policy)
                if saved_name is not None:
                    repository.add_archive_history(title_id, title_name, saved_name, trigger_type)
                    moved += 1
            except Exception as e:
                log.error("rclone 아카이빙 이동 실패 (%s): %s", src, e)
    else:
        dest_dir = resolve_archive_dest(archive_root, title_name, base_path, force_subfolder=force_subfolder)
        for _num, src in files:
            try:
                saved_name = move_file_with_conflict_policy(src, dest_dir, policy)
                if saved_name is not None:
                    repository.add_archive_history(title_id, title_name, saved_name, trigger_type)
                    moved += 1
            except Exception as e:
                log.error("아카이빙 이동 실패 (%s): %s", src, e)
    return moved


def run_periodic_archive(archive_root: str, download_root: str, rclone_config_path: str = "") -> int:
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
        )
    return total


def manual_archive_now(archive_root: str, download_root: str, title_ids: list[str], rclone_config_path: str = "") -> int:
    """수동 실행 — 지정된 것과 동일 규칙(마지막 파일 보존), 대상만 사용자가 고름."""
    policy = get_conflict_policy()
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
            target.dest_base_path, policy, "manual", keep_last=True,
            dest_type=target.dest_type, rclone_config_path=rclone_config_path,
        )
    return total


def process_pending_finish_archives(archive_root: str, download_root: str, rclone_config_path: str = "") -> int:
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
        )
        repository.remove_pending_finish_archive(title_id)
    return total


def bulk_move_folder(archive_root: str, source_path: str, dest_path: str) -> int:
    """1회성 폴더→폴더 전체 이동 (아카이빙 대상 지정 규칙과 무관, 백업 드라이브 정리용).
    ARCHIVE_ROOT 밖을 가리키지 못하게 검증한다."""
    root = Path(archive_root).resolve()
    src = (root / source_path).resolve()
    dest = (root / dest_path).resolve()
    if root not in src.parents and src != root:
        raise ValueError("원본 경로가 아카이브 루트 밖입니다.")
    if root not in dest.parents and dest != root:
        raise ValueError("목적지 경로가 아카이브 루트 밖입니다.")
    if not src.is_dir():
        raise ValueError("원본 폴더가 존재하지 않습니다.")

    dest.mkdir(parents=True, exist_ok=True)
    policy = get_conflict_policy()
    moved = 0
    try:
        entries = list(src.iterdir())
    except OSError as e:
        raise ValueError(f"원본 폴더 목록을 읽을 수 없습니다 (마운트가 불안정할 수 있습니다): {e}")

    for item in entries:
        try:
            if item.is_dir():
                shutil.move(str(item), str(dest / item.name))
                moved += 1
            else:
                saved_name = move_file_with_conflict_policy(item, dest, policy)
                if saved_name is not None:
                    moved += 1
        except OSError as e:
            log.error("일괄 이동 중 개별 항목 실패, 건너뜀 (%s): %s", item, e)
    return moved
