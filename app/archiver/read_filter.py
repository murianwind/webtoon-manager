"""웹툰 뷰어의 읽음 기록으로 아카이빙할 회차를 거른다 — 순수 함수라 뷰어 통신과 파일 이동 어느 쪽도 모른다."""
from __future__ import annotations

from pathlib import Path


def filter_read_files(files: list[tuple[int, Path]], read_numbers: frozenset[int] | None) -> list[tuple[int, Path]]:
    """(회차 번호, 파일) 목록에서 읽은 회차만 남긴다. read_numbers가 None(뷰어 미설정/조회 실패)이면 거르지 않고 그대로 돌려준다."""
    if read_numbers is None:
        return files
    return [(num, path) for num, path in files if num in read_numbers]
