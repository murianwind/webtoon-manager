"""
카카오웹툰 API 클라이언트. 네이버와 결정적으로 다른 점: 작가에게 고유 ID가 없고
이름 문자열만 있다(실제 HAR 응답 3곳 — 요일별 목록, 작품 상세, 검색 — 전부 확인함).
그래서 "이 작가의 모든 작품"은 이름으로 검색하는 방식으로만 구할 수 있다 —
다행히 검색 API가 실제로 완결작까지 전부 포함해서 준다(강풀 작가로 실제 검증:
2000년대 완결작 "순정만화"/"바보"까지 13개 전부 나옴).

검색/작품 조회 API는 실제로 로그인 쿠키 없이도 200으로 응답한다(HAR에서 확인) —
이번 신작 알림 기능 범위에서는 쿠키가 필요 없다.
"""

import logging

import aiohttp

log = logging.getLogger(__name__)

KAKAO_SEARCH_URL = "https://gateway-kw.kakao.com/search/v2/content"
KAKAO_TIMETABLE_URL = "https://gateway-kw.kakao.com/section/v2/timetables/days"

# 네이버의 "요일별 전체목록" 하나에 대응하는 것 — 카카오는 이걸 한 번에 주는 API가
# 없어서, 요일 7개 + 신작 + 완결을 전부 따로 불러서 합쳐야 전체 카탈로그가 된다.
# 실제 HAR로 확인: timetable_completed 하나만 해도 2055개(완결 전체), timetable_tue는
# 147개 — 둘 다 접미사 없는 버전이 필터 없는 전체 목록이다.
_CATALOG_PLACEMENTS = [
    "timetable_mon", "timetable_tue", "timetable_wed", "timetable_thu",
    "timetable_fri", "timetable_sat", "timetable_sun",
    "timetable_new", "timetable_completed",
]

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://webtoon.kakao.com",
    "Referer": "https://webtoon.kakao.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


async def search_by_author(
    session: aiohttp.ClientSession, author_name: str, timeout_seconds: int
) -> list[dict]:
    """
    작가 이름으로 검색해서, 실제로 그 이름이 작가로 걸린 작품만 골라 반환한다.
    검색어가 제목에도 우연히 걸릴 수 있어서(예: 작가 이름이 흔한 단어와 겹치는 경우),
    searchCategory=="AUTHOR"인 것만 먼저 거르고, authors 목록에 그 이름이 실제로
    있는지 한 번 더 확인한다(이중 검증 — 과거 다른 곳에서 느슨한 필터로 문제가 있었던
    전례가 있어서 여기는 처음부터 엄격하게 간다).

    반환값은 원본 dict 리스트 그대로 준다(title_id, title_name, is_adult 정도만
    호출부에서 뽑아 쓰면 됨) — 완결/연재 상태를 구분할 필요가 지금은 없어서
    파싱을 최소화했다.
    """
    async with session.get(
        KAKAO_SEARCH_URL,
        params={"limit": 30, "offset": 0, "word": author_name},
        headers=_HEADERS,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
    ) as response:
        if response.status != 200:
            log.warning("카카오 검색 실패 (author=%s): HTTP %s", author_name, response.status)
            return []
        data = await response.json()

    items = (data.get("data") or {}).get("content") or []
    results = []
    for item in items:
        if item.get("searchCategory") != "AUTHOR":
            continue
        author_names_in_item = {a.get("name") for a in item.get("authors") or []}
        if author_name not in author_names_in_item:
            continue
        title_id = item.get("id")
        if title_id is None:
            continue
        results.append(
            {
                "title_id": int(title_id),
                "title_name": item.get("title", ""),
                "is_adult": bool(item.get("adult")),
            }
        )
    return results


async def fetch_full_catalog(session: aiohttp.ClientSession, timeout_seconds: int) -> list[dict]:
    """
    요일 7개 + 신작 + 완결 placement를 전부 불러서 합친다 — 네이버의 "요일별 전체목록"에
    대응하는, 카카오웹툰의 사실상 전체 카탈로그. 한 placement가 실패해도(네트워크 오류 등)
    나머지는 계속 가져온다 — 완결 목록이 2000개가 넘어서 그것 하나만 잠깐 느려도 다른
    요일 정보까지 전부 날아가면 안 되기 때문.
    """
    all_items: dict[int, dict] = {}
    for placement in _CATALOG_PLACEMENTS:
        try:
            async with session.get(
                KAKAO_TIMETABLE_URL,
                params={"placement": placement},
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status != 200:
                    log.warning("카카오 카탈로그 조회 실패 (placement=%s): HTTP %s", placement, response.status)
                    continue
                data = await response.json()
        except Exception as e:
            log.warning("카카오 카탈로그 조회 예외 (placement=%s): %s", placement, e)
            continue

        groups = data.get("data") or []
        for group in groups:
            for card_group in group.get("cardGroups") or []:
                for card in card_group.get("cards") or []:
                    content = card.get("content") or {}
                    title_id = content.get("id")
                    if title_id is None:
                        continue
                    all_items[title_id] = {
                        "title_id": title_id,
                        "title_name": content.get("title", ""),
                        "is_adult": bool(content.get("adult")),
                        "author_names": [
                            a.get("name") for a in content.get("authors") or [] if a.get("type") == "AUTHOR" and a.get("name")
                        ],
                    }
    return list(all_items.values())


def extract_candidate_author_names(items: list[dict]) -> list[str]:
    """전체 카탈로그에서 AUTHOR 타입 이름만 뽑아 중복 제거한다 (PUBLISHER/ILLUSTRATOR
    전용 이름은 제외 — 그렇게 안 하면 "카카오웹툰 스튜디오" 같은 게 후보에 계속 낀다)."""
    names: set[str] = set()
    for item in items:
        names.update(item.get("author_names") or [])
    return sorted(names)
