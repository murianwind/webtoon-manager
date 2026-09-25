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

# 저자 목록(authors)의 type 값 — 대부분은 그냥 "AUTHOR"인데, 원작 기반 작품은
# 그림/원작이 나뉘어서 "ILLUSTRATOR"/"ORIGINAL_STORY"로만 들어오는 경우가 실제로
# 있다(예: "아기님 캐시로 로판 달린다" — AUTHOR 타입이 아예 없고 ILLUSTRATOR/
# ORIGINAL_STORY/PUBLISHER만 있음, 실제 HAR로 확인). "AUTHOR"만 보면 이런 작품은
# 저자가 통째로 안 뽑혀서 화면에 아무것도 안 나온다 — PUBLISHER(플랫폼/출판사)는
# 창작자가 아니라서 제외한다.
_AUTHOR_LIKE_TYPES = {"AUTHOR", "ILLUSTRATOR", "ORIGINAL_STORY"}

# "웹툰 전체목록"에 보여줄 건 신작/완결까지 다 필요 없고, 지금 연재 중인(요일 배정된)
# 것만이면 된다 — 요일 7개만 따로 뽑아둔다(위 _CATALOG_PLACEMENTS의 부분집합).
_WEEKDAY_PLACEMENTS = _CATALOG_PLACEMENTS[:7]

KAKAO_EPISODE_LIST_URL_TMPL = "https://gateway-kw.kakao.com/episode/v2/views/content-home/contents/{content_id}/episodes"
KAKAO_CONTENT_URL_TMPL = "https://webtoon.kakao.com/content/{seo_id}/{content_id}"
KAKAO_VIEWER_URL_TMPL = "https://webtoon.kakao.com/viewer/{episode_seo_id}/{episode_id}"

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


async def _fetch_placement_cards(session: aiohttp.ClientSession, placement: str, timeout_seconds: int) -> list[dict]:
    """한 placement의 원본 카드 리스트(파싱 전)를 그대로 반환한다. 실패하면 빈 리스트 —
    호출부가 "이 placement 하나 실패해도 나머지는 계속" 정책을 그대로 유지할 수 있게."""
    try:
        async with session.get(
            KAKAO_TIMETABLE_URL,
            params={"placement": placement},
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                log.warning("카카오 목록 조회 실패 (placement=%s): HTTP %s", placement, response.status)
                return []
            data = await response.json()
    except Exception as e:
        log.warning("카카오 목록 조회 예외 (placement=%s): %s", placement, e)
        return []

    cards = []
    for group in data.get("data") or []:
        for card_group in group.get("cardGroups") or []:
            cards.extend(card_group.get("cards") or [])
    return cards


async def fetch_weekday_catalog(session: aiohttp.ClientSession, timeout_seconds: int) -> list[dict]:
    """"웹툰 전체목록"에 보여줄 것 — 요일 7개(지금 연재 중인 것)만 훑는다. 신작/완결
    placement는 안 써서 fetch_full_catalog보다 훨씬 가볍다.

    UP/신작/휴재 전부 이 한 번의 조회(placement당 한 번, 접미사 없는 "전체")로 뽑을 수
    있다 — badges에 type=="UP"이면 새 회차, type=="NEW"면 신작, title=="EPISODES_NOT_PUBLISHING"
    이면 휴재(실제 HAR로 셋 다 같은 응답 안에서 확인함: 화요일 캡처엔 UP이 하나도 없어서
    한동안 "UP은 다른 placement에만 있다"고 잘못 판단했었는데, 목요일 캡처엔 UP/신작/휴재
    배지가 전부 이 접미사 없는 placement 응답에 같이 들어있었다 — 그날 그 요일에 해당하는
    게 마침 없었을 뿐, placement 자체의 제약이 아니었다)."""
    all_items: dict[int, dict] = {}
    for placement in _WEEKDAY_PLACEMENTS:
        for card in await _fetch_placement_cards(session, placement, timeout_seconds):
            content = card.get("content") or {}
            title_id = content.get("id")
            if title_id is None:
                continue
            badges = content.get("badges") or []
            badge_types = {b.get("type") for b in badges}
            badge_titles = {b.get("title") for b in badges}
            all_items[title_id] = {
                "title_id": title_id,
                "title_name": content.get("title", ""),
                "seo_id": content.get("seoId", ""),
                "is_adult": bool(content.get("adult")),
                "author_names": [
                    a.get("name") for a in content.get("authors") or [] if a.get("type") in _AUTHOR_LIKE_TYPES and a.get("name")
                ],
                "has_update": "UP" in badge_types,
                "is_new": "NEW" in badge_types,
                "is_paused": "EPISODES_NOT_PUBLISHING" in badge_titles,
                "thumbnail_url": content.get("backgroundImage") or "",
            }
    return list(all_items.values())


async def fetch_latest_episode_url(
    session: aiohttp.ClientSession, content_id: int, timeout_seconds: int
) -> str | None:
    """이 작품의 가장 최근 회차로 바로 가는 뷰어 URL을 만든다 — 회차 목록을
    번호 내림차순(sort=-NO)으로 1페이지만 조회하면 맨 앞이 최신 회차다(실제 HAR로
    확인). "다운로드 리포트"에서 UP 배지가 있는 작품에만 이걸 조회하므로, 전체
    카탈로그 규모와 무관하게 가볍다."""
    try:
        async with session.get(
            KAKAO_EPISODE_LIST_URL_TMPL.format(content_id=content_id),
            params={"sort": "-NO", "offset": 0, "limit": 1},
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                log.warning("카카오 회차 목록 조회 실패 (content_id=%s): HTTP %s", content_id, response.status)
                return None
            data = await response.json()
    except Exception as e:
        log.warning("카카오 회차 목록 조회 예외 (content_id=%s): %s", content_id, e)
        return None

    episodes = ((data.get("data") or {}).get("episodes")) or []
    if not episodes:
        return None
    latest = episodes[0]
    episode_id = latest.get("id")
    episode_seo_id = latest.get("seoId")
    if episode_id is None or not episode_seo_id:
        return None
    return KAKAO_VIEWER_URL_TMPL.format(episode_seo_id=episode_seo_id, episode_id=episode_id)


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
                            a.get("name") for a in content.get("authors") or [] if a.get("type") in _AUTHOR_LIKE_TYPES and a.get("name")
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
