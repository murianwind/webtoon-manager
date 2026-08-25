"""
로컬에 이미 받아둔 마지막 회차가 실제 네이버 회차 번호(no)로 몇 화인지 찾는다.

중요: zip 파일명 맨 앞의 숫자(예: "109 소년교도소 2부 9화.zip"의 109)는 실제 네이버
회차 번호가 아니라, change.py 시절부터 이어져 온 "끊김 없는 압축파일 일련번호"일
뿐이다. 네이버 쪽 회차 번호(no)는 이따금 건너뛴다(...109, 111...처럼). 그래서
"로컬에 zip이 N개 있으니 N번째로 받은 것"처럼 위치로 추론하면, 그 사이에 번호가
한 번이라도 건너뛴 적이 있는 순간 어긋난다.

그래서 파일명 앞자리 숫자는 무시하고, 파일명에 남아있는 "부제목" 텍스트를 현재
네이버 회차 목록의 subtitle과 대조해서 실제로 몇 화까지 받았는지 찾는다 — 기존
download.py의 get_last_subtitle + normalize 매칭 방식과 같은 원리다.
"""

import re
from pathlib import Path

from app.models import EpisodeInfo

_LEADING_SEQ_NUMBER_RE = re.compile(r"^\d+\s*")
_NON_CORE_CHARS_RE = re.compile(r"[^0-9a-zA-Z가-힣]")


def _normalize_for_match(text: str) -> str:
    """비교용 정규화: 공백/특수문자/치환문자 차이를 무시하고 한글·영문·숫자만 남긴다."""
    return _NON_CORE_CHARS_RE.sub("", text or "")


def _latest_zip_filename(webtoon_dir: Path) -> str | None:
    """파일명 앞 일련번호가 가장 큰(=가장 최근에 만들어진) zip의 stem을 반환한다."""
    zip_files = [p for p in webtoon_dir.iterdir() if p.is_file() and p.suffix.lower() == ".zip"]
    if not zip_files:
        return None

    def seq_number(p: Path) -> int:
        m = re.match(r"^(\d+)", p.stem)
        return int(m.group(1)) if m else -1

    zip_files.sort(key=seq_number)
    return zip_files[-1].stem


def find_last_downloaded_episode_no(webtoon_dir: Path, episodes: list[EpisodeInfo]) -> int:
    """
    webtoon_dir에 있는 마지막 zip 파일명에서 부제목을 뽑아 episodes(오름차순, 실제 no 포함)와
    대조해서 실제 회차 번호를 찾는다. 매칭되는 게 없으면(=아직 하나도 안 받았거나 부제목이
    너무 달라졌거나) 0을 반환한다 — 이 경우 호출자는 기존에 DB에 저장된 값을 그대로 쓴다.
    """
    if not webtoon_dir.is_dir() or not episodes:
        return 0

    last_stem = _latest_zip_filename(webtoon_dir)
    if last_stem is None:
        return 0

    stem_without_seq = _LEADING_SEQ_NUMBER_RE.sub("", last_stem)
    normalized_filename = _normalize_for_match(stem_without_seq)
    if not normalized_filename:
        return 0

    # 최신(큰 no)부터 검사해서, 파일명에 부제목이 포함된 것 중 가장 회차가 큰 것을 찾는다.
    for episode in reversed(episodes):
        normalized_subtitle = _normalize_for_match(episode.subtitle)
        if normalized_subtitle and normalized_subtitle in normalized_filename:
            return episode.episode_no

    return 0
