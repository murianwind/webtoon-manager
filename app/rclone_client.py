"""
rclone 원격(리모트)을 로컬 폴더와 동일한 인터페이스로 다루기 위한 얇은 래퍼.
전부 `rclone` CLI를 서브프로세스로 호출한다 (Windows 마운트를 거치지 않아서,
Docker Desktop이 WinFsp 가상 드라이브를 못 읽는 문제 자체를 우회한다 — 실제로
겪은 문제였음).

목적지는 항상 "remote이름:경로" 형태의 문자열 하나로 다룬다 (rclone 자체의
표기법과 동일) — archiver.py가 로컬 Path와 나란히 다루기 쉽게 하기 위함.
"""

import json
import logging
import subprocess

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30
# 파일 업로드(moveto)는 폴더 조회 같은 가벼운 명령과 성격이 다르다 — 대용량
# 파일을 느린 원격(원드라이브 등)에 올리는 거라 30초를 훌쩍 넘길 수 있는데,
# 그 짧은 타임아웃 때문에 "실제로는 업로드+원본삭제까지 다 끝났는데 rclone이
# 마지막 정리 단계에서 죽어서 실패로 잘못 기록되는" 문제가 실제로 있었다.
_UPLOAD_TIMEOUT_SECONDS = 1800


class RcloneError(Exception):
    pass


def _run(config_path: str, args: list[str], timeout: int = _TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(
            ["rclone", "--config", config_path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RcloneError("rclone 프로그램을 찾을 수 없습니다 (이미지에 설치되어 있어야 합니다).")
    except subprocess.TimeoutExpired:
        raise RcloneError(f"rclone 응답이 {timeout}초 내에 오지 않았습니다 (원격 저장소 연결 상태를 확인하세요).")
    if result.returncode != 0:
        raise RcloneError(result.stderr.strip() or f"rclone 명령 실패 (종료코드 {result.returncode})")
    return result.stdout


def list_remotes(config_path: str) -> list[str]:
    """conf에 등록된 원격 이름 목록. 콜론(:)까지 포함해서 나오므로 제거한다."""
    output = _run(config_path, ["listremotes"])
    return [line.strip().rstrip(":") for line in output.splitlines() if line.strip()]


def list_folders(config_path: str, remote: str, path: str) -> list[dict]:
    """remote:path 밑의 하위 폴더 이름만 나열한다 (--dirs-only, 각 폴더 내부를
    들여다보지 않음). 각 폴더가 "비어있는지"는 여기서 확인하지 않는다 —

    예전엔 목록을 보여줄 때 하위 폴더 전부를 미리 확인했는데, 이게 두 가지
    문제를 낳았다: (1) 폴더가 많으면 그만큼 오래 걸리고, 문제있는 폴더 하나
    때문에 전체가 실패하는 경우 폴더 개수만큼 순서대로 재시도하느라 몇 분씩
    걸리는 사례가 실제로 있었다. (2) 애초에 사용자가 클릭하지도 않을 폴더까지
    전부 확인하는 건 낭비다. 그래서 "비어있는지" 확인은 사용자가 실제로 그
    폴더를 선택하려고 클릭한 시점에, 그 폴더 하나만(is_folder_empty) 하도록
    분리했다 — 폴더가 몇 개든 목록 조회 자체는 항상 빠른 단일 호출로 끝난다."""
    target = f"{remote}:{path}" if path else f"{remote}:"
    dirs_output = _run(config_path, ["lsjson", target, "--dirs-only"])
    depth1_dirs = json.loads(dirs_output)
    folders = []
    for entry in depth1_dirs:
        rel = f"{path}/{entry['Name']}" if path else entry["Name"]
        folders.append({"name": entry["Name"], "path": rel})
    return folders


def is_folder_empty(config_path: str, remote: str, path: str) -> bool:
    """이미 파일이 하나라도 있으면 False — 로컬의 is_folder_selectable_as_dest와
    동일한 규칙. 사용자가 특정 폴더를 실제로 선택하려는 시점에, 그 폴더 딱
    하나에 대해서만 호출된다(목록 조회 시점엔 안 씀 — list_folders 설명 참고)."""
    target = f"{remote}:{path}" if path else f"{remote}:"
    try:
        output = _run(config_path, ["lsjson", target])
    except RcloneError as e:
        log.warning("rclone 폴더 내용 확인 실패, 안전하게 선택 불가 처리 (%s): %s", target, e)
        return False
    entries = json.loads(output)
    return len(entries) == 0


def create_folder(config_path: str, remote: str, path: str) -> None:
    target = f"{remote}:{path}"
    _run(config_path, ["mkdir", target])


def move_file_to_remote(config_path: str, local_file_path: str, remote: str, dest_path: str, dest_file_name: str) -> None:
    """로컬 파일 하나를 원격의 지정한 파일명으로 업로드하고, 성공하면 로컬 원본을 지운다
    (rclone moveto가 원자적으로 처리). 충돌 정책(덮어쓰기/건너뛰기/이름변경)은 호출부(archiver.py)가
    is_folder_empty 등으로 미리 확인한 뒤 최종 파일명을 정해서 넘겨준다."""
    target = f"{remote}:{dest_path}/{dest_file_name}" if dest_path else f"{remote}:{dest_file_name}"
    _run(config_path, ["moveto", local_file_path, target], timeout=_UPLOAD_TIMEOUT_SECONDS)


def file_exists(config_path: str, remote: str, path: str, file_name: str) -> bool:
    target = f"{remote}:{path}" if path else f"{remote}:"
    try:
        output = _run(config_path, ["lsjson", target])
    except RcloneError:
        return False
    entries = json.loads(output)
    return any(e["Name"] == file_name for e in entries)


def list_files_recursive(config_path: str, remote: str, path: str) -> list[str]:
    """remote:path 밑의 모든 파일을 하위 폴더까지 재귀적으로 나열해서, 파일만
    상대경로(예: 'sub/1.zip')로 반환한다. 일괄 이동이 "파일 단위"로 옮기기 위한
    목록 수집용 — 디렉터리 항목은 제외한다(--files-only)."""
    target = f"{remote}:{path}" if path else f"{remote}:"
    try:
        output = _run(config_path, ["lsjson", target, "--recursive", "--files-only"])
    except RcloneError as e:
        raise RcloneError(f"'{target}' 목록을 읽을 수 없습니다: {e}")
    entries = json.loads(output)
    return [e["Path"] for e in entries]


def moveto(config_path: str, src_spec: str, dest_spec: str) -> None:
    """src_spec을 dest_spec으로 옮긴다. 둘 중 하나(또는 둘 다)가 'remote:path' 형태면
    rclone이 원격으로 처리하고, 콜론이 없는 일반 경로는 로컬 파일시스템으로 처리한다
    (rclone 자체 규칙) — 로컬↔원격, 원격↔원격 이동을 전부 이 한 함수로 커버한다."""
    _run(config_path, ["moveto", src_spec, dest_spec], timeout=_UPLOAD_TIMEOUT_SECONDS)


def copyto(config_path: str, src_spec: str, dest_spec: str) -> None:
    """moveto와 동일하지만 원본을 지우지 않는다 — 주기 아카이빙에서 info.xml/커버를
    "복사만"할 때 쓴다(원본 다운로드 폴더 쪽에도 계속 남아있어야 하므로)."""
    _run(config_path, ["copyto", src_spec, dest_spec], timeout=_UPLOAD_TIMEOUT_SECONDS)


def rmdirs_if_empty(config_path: str, remote: str, path: str) -> None:
    """remote:path 이하(자기 자신 포함)에서 파일이 하나도 없는 폴더를 정리한다.
    이동 후 빈 껍데기 폴더가 원격에 계속 남는 걸 막기 위한 후처리용이라, 실패해도
    (원격 특이 폴더 등) 전체 이동 자체를 실패로 취급하지 않고 로그만 남긴다."""
    if not path:
        return  # remote 루트 자체는 정리 대상이 아님
    target = f"{remote}:{path}"
    try:
        _run(config_path, ["rmdirs", target])
    except RcloneError as e:
        log.warning("빈 폴더 정리 실패 (무시하고 계속): %s: %s", target, e)
