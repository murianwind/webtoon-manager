"""카카오 웹툰 중 앱에서 다루지 않는 작품을 가려내는 기준 — 제목에 "단행본"이 들어간 것(연재 웹툰이 아니라 묶음/단행본 판매 작품)은 전체목록, 제외됨/구독해제, 수동 다운로드,
작가 신작 구독, 신작 알림 등 어디에도 나오지 않는다. 카카오에서 받아오는 곳(요일 목록/검색 카드, 목록 캐시)에서 처음부터 빼고, DB에 남은 기록이나 번호로 직접 여는 경로는 쓰는 곳에서 뺀다.
기준은 제목에 이 글자가 그대로 들어 있는지다(공백을 끼운 "단 행 본" 등은 해당하지 않는다). 다른 단어도 빼야 하면 EXCLUDED_TITLE_WORDS에 더한다."""

EXCLUDED_TITLE_WORDS = ("단행본",)
EXCLUDED_MESSAGE = "제목에 '단행본'이 들어간 작품은 지원하지 않습니다."


def is_excluded_title(title: str | None) -> bool:
    return bool(title) and any(word in title for word in EXCLUDED_TITLE_WORDS)
