"""
카카오페이지 작품 "정보"(author_list)에서 글/그림/원작 작가를 읽는 규칙 — 관심 작가 등록, info.xml, 파일명 템플릿의 {author}가 모두 이걸 쓴다.

- 한 항목의 이름에 여러 명이 들어 있는 경우가 있어서(예: 원작 항목 하나에 "연상호, 민홍남, 황은영") 쉼표(전각 포함)로 나눠 한 명씩 다룬다.
- 같은 이름은 역할 안에서 한 번만, 순서는 유지한다.
- 관심 작가로 등록할 이름: 원작자가 있으면 원작자 전원, 없으면 글 작가 전원(네이버와 같은 규칙, 그림 작가는 제외).
"""

import re

_NAME_SEPARATORS = re.compile(r"[,，]")
ROLES = {"writer": 0, "illustrator": 1, "original_author": 2}


def split_names(name: str | None) -> list[str]:
    """"A, B，C" → ["A", "B", "C"] (공백 정리, 빈 값 제거)."""
    return [n.strip() for n in _NAME_SEPARATORS.split(name or "") if n.strip()]


def split_authors(about: dict | None) -> tuple[list[str], list[str], list[str]]:
    """작품 "정보"의 author_list에서 (글, 그림, 원작) 이름 목록."""
    groups: tuple[list[str], list[str], list[str]] = ([], [], [])
    for author in (about or {}).get("author_list") or []:
        index = ROLES.get(author.get("role"))
        if index is None:
            continue
        for name in split_names(author.get("name")):
            if name not in groups[index]:
                groups[index].append(name)
    return groups


def authors_to_register(about: dict | None) -> list[str]:
    """구독할 때/재동기화 때 관심 작가로 등록할 이름 — 원작자가 있으면 원작자, 없으면 글 작가."""
    writers, _, originals = split_authors(about)
    return originals or writers
