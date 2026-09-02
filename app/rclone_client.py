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


class RcloneError(Exception):
    pass


def _run(config_path: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["rclone", "--config", config_path, *args],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise RcloneError("rclone 프로그램을 찾을 수 없습니다 (이미지에 설치되어 있어야 합니다).")
    except subprocess.TimeoutExpired:
        raise RcloneError("rclone 응답이 시간 내에 오지 않았습니다 (원격 저장소 연결 상태를 확인하세요).")
    if result.returncode != 0:
        raise RcloneError(result.stderr.strip() or f"rclone 명령 실패 (종료코드 {result.returncode})")
    return result.stdout


def list_remotes(config_path: str) -> list[str]:
    """conf에 등록된 원격 이름 목록. 콜론(:)까지 포함해서 나오므로 제거한다."""
    output = _run(config_path, ["listremotes"])
    return [line.strip().rstrip(":") for line in output.splitlines() if line.strip()]


def list_folders(config_path: str, remote: str, path: str) -> list[dict]:
    """remote:path 밑의 하위 폴더만 나열한다. 각 폴더가 비어있는지(선택 가능한지)도 같이 확인한다."""
    target = f"{remote}:{path}" if path else f"{remote}:"
    output = _run(config_path, ["lsjson", target, "--dirs-only"])
    entries = json.loads(output)
    folders = []
    for entry in entries:
        rel = f"{path}/{entry['Name']}" if path else entry["Name"]
        selectable = is_folder_empty(config_path, remote, rel)
        folders.append({"name": entry["Name"], "path": rel, "selectable": selectable})
    return folders


def is_folder_empty(config_path: str, remote: str, path: str) -> bool:
    """이미 파일이 하나라도 있으면 False — 로컬의 is_folder_selectable_as_dest와 동일한 규칙."""
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
    _run(config_path, ["moveto", local_file_path, target])


def file_exists(config_path: str, remote: str, path: str, file_name: str) -> bool:
    target = f"{remote}:{path}" if path else f"{remote}:"
    try:
        output = _run(config_path, ["lsjson", target])
    except RcloneError:
        return False
    entries = json.loads(output)
    return any(e["Name"] == file_name for e in entries)
