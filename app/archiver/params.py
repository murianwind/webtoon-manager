"""아카이빙 매개변수 객체 — 인자가 14~16개까지 늘어났던 함수들의 값을 이름 붙은 묶음으로 만든 것.

- `ProgressReport`: 진행 문구 / 이름 충돌 / 이동 실패를 호출부에 알리는 통로(셋 다 선택). 함수 안에서 "지정했으면"을 매번 확인하지 않고 그냥 부른다.
- `TitleArchiveJob`: 작품 하나를 아카이빙하는 데 필요한 값(`_archive_title`).
- `TitleDestination`: 작품의 회차 파일을 어디로 어떻게 옮기는지(`_move_title_files`).
- `FolderMove`: 폴더 대상의 회차 파일을 어디서 어디로 어떻게 옮기는지(`_move_folder_files`).

다른 모듈을 가져오지 않는 맨 아래 계층이라 어디서든 가져다 쓸 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class ProgressReport:
    progress_callback: Callable[[str], None] | None = None
    conflict_log: list | None = None
    failure_log: list | None = None

    def progress(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

    def conflict(self, title_name: str, file_name: str, policy: str) -> None:
        if self.conflict_log is not None:
            self.conflict_log.append((title_name, file_name, policy))

    def failure(self, title_name: str, file_name: str, reason: str) -> None:
        if self.failure_log is not None:
            self.failure_log.append((title_name, file_name, reason))


@dataclass(frozen=True)
class TitleArchiveJob:
    archive_root: str
    download_root: str
    title_id: str
    title_name: str
    base_path: str
    policy: str
    trigger_type: str
    keep_last: bool
    dest_type: str = "local"
    rclone_config_path: str = ""
    writer_names: list[str] | None = None
    filename_template_preset_id: int | None = None
    kakao: bool = False
    read_numbers: frozenset[int] | None = None  # 뷰어에서 읽은 회차 번호 — 지정하면 이 회차만 옮긴다(None이면 거르지 않음)


@dataclass(frozen=True)
class TitleDestination:
    title_id: str
    title_name: str
    dest_type: str
    policy: str
    trigger_type: str
    template: str
    writer_names: list[str]
    rclone_config_path: str
    remote: str
    rclone_dest_path: str
    local_dest_dir: Path | None


@dataclass(frozen=True)
class FolderMove:
    target: Any
    src_kind: str
    src_ctx: Any
    dest_kind: str
    dest_ctx: Any
    policy: str
    template: str
    display_name: str
    trigger_type: str
    rclone_config_path: str
