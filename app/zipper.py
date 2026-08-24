"""
회차 폴더를 zip으로 묶는 로직. 기존 change.py의 번호 이어붙이기 규칙과
"압축 후 원본 폴더 삭제" 동작을 그대로 유지한다 (요구사항: 네이밍/동작 유지).

원본 change.py는 현재 작업 디렉토리(cwd) 전체를 os.walk로 훑었지만, 여기서는
호출자가 넘긴 웹툰 루트 폴더 하나만 대상으로 한다 — 동작은 동일하고 범위만 명시적.
"""

import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

_BRACKET_NUMBER_RE = re.compile(r"\[(\d+)\]")
_LEADING_DIGITS_RE = re.compile(r"^(\d+)")


def _clean_name(name: str) -> str:
    return name.replace("[", "").replace("]", "").replace("：", " ").replace("  ", " ")


def _next_zip_number(parent_dir: Path, folder_num: int) -> int:
    """parent_dir에 이미 있는 zip 파일들의 번호를 보고, 번호가 끊기지 않게 이어붙인다."""
    existing_nums = []
    for entry in os.listdir(parent_dir):
        if entry.endswith(".zip"):
            match = _LEADING_DIGITS_RE.match(entry)
            if match:
                existing_nums.append(int(match.group(1)))

    if not existing_nums:
        return folder_num

    last_zip_num = max(existing_nums)
    if folder_num != last_zip_num + 1:
        return last_zip_num + 1
    return folder_num


def zip_episode_folders(webtoon_dir: Path) -> list[str]:
    """
    webtoon_dir 아래의 '[번호] 부제목' 형식 회차 폴더들을 각각 zip으로 압축하고
    원본 폴더는 삭제한다. 생성된 zip 파일명 목록을 반환한다.
    """
    if not webtoon_dir.is_dir():
        return []

    created_zip_names: list[str] = []

    # 주의: 일부러 [번호]로 정렬하지 않는다. 네이버 회차 번호 자체가 가끔 중간에
    # 어긋나는 경우가 있어서, 폴더 생성(=다운로드) 순서를 실제 순서로 신뢰하고
    # os.walk가 나열하는 순서 그대로 처리한다 (change.py 원본 동작과 동일).
    episode_dirs: list[Path] = []
    for dirpath, dirnames, _files in os.walk(webtoon_dir):
        for dirname in dirnames:
            episode_dirs.append(Path(dirpath) / dirname)

    for episode_dir in episode_dirs:
        match = _BRACKET_NUMBER_RE.search(episode_dir.name)
        if not match:
            continue

        parent_dir = episode_dir.parent
        folder_num = int(match.group(1))
        assigned_num = _next_zip_number(parent_dir, folder_num)
        number_prefix = str(assigned_num).zfill(3)

        parent_dir_label = _clean_name(parent_dir.name)
        # '[0001] 부제목' -> 첫 공백 뒤의 '부제목'만 추출
        if " " in episode_dir.name:
            subtitle_part = _clean_name(episode_dir.name.split(" ", 1)[1])
        else:
            subtitle_part = _clean_name(episode_dir.name)

        zip_filename = f"{number_prefix} {parent_dir_label} {subtitle_part}.zip"
        zip_path = parent_dir / zip_filename

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(episode_dir):
                    for file_name in files:
                        file_path = Path(root) / file_name
                        zf.write(file_path, file_path.relative_to(episode_dir))

            shutil.rmtree(episode_dir)
            created_zip_names.append(zip_filename)
            log.info("압축 완료: %s", zip_filename)
        except Exception as e:
            log.error("압축 중 오류 발생 (%s): %s", episode_dir, e)

    return created_zip_names
