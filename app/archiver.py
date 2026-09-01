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

from app import repository
from app.file_utils import remove_forbidden_str
from app.zipper import _LEADING_DIGITS_RE

log = logging.getLogger(__name__)

_CONFLICT_POLICY_SETTING_KEY = "archive_conflict_policy"
_DEFAULT_BASE_PATH_SETTING_KEY = "archive_default_base_path"
_ON_FINISH_UNSUBSCRIBE_SETTING_KEY = "archive_on_finish_unsubscribe"


def _list_episode_files_sorted(title_dir: Path) -> list[tuple[int, Path]]:
    """제목 폴더 바로 밑의 zip 파일들을, 파일명 맨 앞 번호 기준으로 오름차순 정렬해서 반환한다."""
    if not title_dir.is_dir():
        return []
    results = []
    for entry in title_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".zip":
            continue
        match = _LEADING_DIGITS_RE.match(entry.name)
        if match:
            results.append((int(match.group(1)), entry))
    results.sort(key=lambda pair: pair[0])
    return results


def is_folder_selectable_as_dest(archive_root: str, base_path: str) -> bool:
    """이미 파일이 하나라도 있으면 새 목적지로 선택 불가 (뒤섞임 방지)."""
    dest = Path(archive_root) / base_path
    if not dest.exists():
        return True
    return not any(dest.iterdir())


def resolve_archive_dest(archive_root: str, title_name: str, base_path: str, *, force_subfolder: bool) -> Path:
    """실제 저장 위치를 계산하고 폴더를 만들어서 반환한다.
    force_subfolder=True면(완결 자동이동의 기본 경로) 항상 웹툰별 서브폴더를 만들고,
    아니면 이 base_path를 지정해서 쓰는 활성 웹툰이 2개 이상일 때만 서브폴더를 만든다."""
    if force_subfolder:
        use_subfolder = True
    else:
        sharing_count = repository.count_archive_targets_with_base_path(base_path)
        use_subfolder = sharing_count > 1

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


def is_finish_unsubscribe_archiving_enabled() -> bool:
    return repository.get_setting(_ON_FINISH_UNSUBSCRIBE_SETTING_KEY) == "1"


def _archive_title(
    archive_root: str, download_root: str, title_id: str, title_name: str,
    base_path: str, policy: str, trigger_type: str, keep_last: bool,
) -> int:
    """실제로 파일들을 옮기고 이력을 남긴다. 반환값은 옮긴 개수."""
    title_dir = Path(download_root) / remove_forbidden_str(title_name)
    files = _list_episode_files_sorted(title_dir)
    if keep_last and len(files) > 0:
        files = files[:-1]  # 마지막(가장 큰 번호)은 보존

    if not files:
        return 0

    force_subfolder = base_path == get_default_base_path()
    dest_dir = resolve_archive_dest(archive_root, title_name, base_path, force_subfolder=force_subfolder)

    moved = 0
    for _num, src in files:
        try:
            saved_name = move_file_with_conflict_policy(src, dest_dir, policy)
            if saved_name is not None:
                repository.add_archive_history(title_id, title_name, saved_name, trigger_type)
                moved += 1
        except Exception as e:
            log.error("아카이빙 이동 실패 (%s): %s", src, e)
    return moved


def run_periodic_archive(archive_root: str, download_root: str) -> int:
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
        )
    return total


def manual_archive_now(archive_root: str, download_root: str, title_ids: list[str]) -> int:
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
        )
    return total


def archive_all_for_finished_unsubscribe(archive_root: str, download_root: str, title_id: str) -> int:
    """완결 후 구독해제 시 자동 전체이동 — 지정돼 있으면 그 경로, 아니면 기본 경로.
    마지막 파일 예외 없이 전부 옮긴다."""
    if not is_finish_unsubscribe_archiving_enabled():
        return 0

    wt = repository.get(title_id)
    if wt is None:
        return 0

    target = repository.get_archive_target(title_id)
    if target is not None and target.enabled:
        base_path = target.dest_base_path
    else:
        base_path = get_default_base_path()
        if not base_path:
            log.warning("완결 자동이동 기본 경로가 설정 안 되어 있어 건너뜀 (title_id=%s)", title_id)
            return 0

    policy = get_conflict_policy()
    return _archive_title(
        archive_root, download_root, title_id, wt.title,
        base_path, policy, "finish_unsubscribe", keep_last=False,
    )


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
    for item in list(src.iterdir()):
        if item.is_dir():
            shutil.move(str(item), str(dest / item.name))
            moved += 1
        else:
            saved_name = move_file_with_conflict_policy(item, dest, policy)
            if saved_name is not None:
                moved += 1
    return moved
