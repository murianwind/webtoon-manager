"""
로컬 폴더에 이미 받아둔 회차 수를 세어, DB에 아직 기록되지 않은 기존 다운로드
(예: 이전 시스템에서 넘어온 웹툰)의 진행 상태를 한 번 추론하는 데 쓴다.

무료 회차는 항상 1화부터 끊김 없이 이어진다는 전제(썸네일 잠금을 만나면 그 이후는
목록에 아예 안 나옴)를 이용해서, "폴더에 있는 회차 수 = 몇 화까지 받았는지"로
바로 환산한다. 회차 번호나 부제목 텍스트를 직접 비교하지 않아 훨씬 단순하고,
파일명 정규화 문제(특수문자 치환 등)에서 자유롭다.
"""

import re
from pathlib import Path

_BRACKET_NUMBER_RE = re.compile(r"\[(\d+)\]")


def count_existing_episode_entries(webtoon_dir: Path) -> int:
    """webtoon_dir 아래의 회차 zip 파일 + (아직 압축 안 된) 회차 폴더 개수를 센다."""
    if not webtoon_dir.is_dir():
        return 0

    count = 0
    for entry in webtoon_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".zip":
            count += 1
        elif entry.is_dir() and _BRACKET_NUMBER_RE.search(entry.name):
            count += 1
    return count
