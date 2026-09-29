"""
카카오페이지 API 클라이언트 (카카오웹툰이 카카오페이지로 통합되면서 옛 gateway-kw.kakao.com
API를 대체한다). 실제 브라우저 캡처(HAR/cURL)로 확인한 것들:

- 로그인 쿠키 없이도 200으로 응답한다(캡처에 islogin: "n"으로 확인됨) — 쿠키는 안 쓴다.
- 웹툰과 웹소설/책이 같은 서비스라 목록/검색 결과에 섞여 나온다 → category_uid == 10(웹툰)만 쓴다.
- 작품 ID는 series_id, 회차 ID는 product_id.
- 요일별 목록은 요일 탭(tab_uid 1~7 = 월~일, 11 = 신작, 12 = 완결)마다 25개씩 page=0,1,2…로
  이어 받는다(사이트는 무한 스크롤이지만 실제로는 이 page 값을 올려 호출하는 것).
  응답의 is_end가 true가 될 때까지 넘긴다.
- badge: "BT02" = 새 회차(UP), "BT03" = 신작 (사이트 화면과 대조해서 확인됨).
- on_issue: "Y" = 연재 중, "P" = 휴재(장기 미갱신 작품에 붙음), "N" = 완결.
- 저자는 authors 필드에 역할 구분 없는 "A,B,C" 문자열 하나로 온다(글/그림/원작은 별도의
  content/about API에서만 구분됨 — 지금은 쓰지 않는다).
"""

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

_BFF = "https://bff-page.kakao.com/api/gateway"
KAKAO_DAYOFWEEK_URL = f"{_BFF}/view/v2/landing/dayofweek"
KAKAO_PRODUCT_LIST_URL = f"{_BFF}/api/v2/content/product/list"
KAKAO_SEARCH_URL = f"{_BFF}/api/v2/search/series"

# 뷰어(회차) 바로가기 — 실제 사이트 주소 형태
KAKAO_VIEWER_URL_TMPL = "https://page.kakao.com/content/{series_id}/viewer/{product_id}"
# 이미지는 kid 값만 있으면 API 호출 없이 바로 조합되는 직접 URL이다. filename으로 크기를 고른다 —
# "o1"은 원본, "o1/dims/resize/384"는 가로 384px로 줄인 것(목록 카드용).
KAKAO_IMAGE_URL_TMPL = "https://page-images.kakaoentcdn.com/download/resource?kid={kid}&filename={filename}"
IMAGE_FILENAME_ORIGINAL = "o1"
IMAGE_FILENAME_CARD = "o1/dims/resize/384"

WEBTOON_CATEGORY_UID = 10
_WEEKDAY_TAB_UIDS = (1, 2, 3, 4, 5, 6, 7)  # 월~일
_SCREEN_UID = 52  # 요일연재 화면
_MAX_PAGES_PER_TAB = 40  # is_end가 안 오는 이상 응답에 대비한 안전 상한(요일당 200~250개 수준이 정상)
_MAX_SEARCH_PAGES = 10
_TAB_CONCURRENCY = 3  # 요일 탭 동시 조회 수 — 예전에 서버에서 연달아 조회하면 HTTP 403이 났던 전례가 있어 낮게
_REQUEST_INTERVAL_SECONDS = 0.3  # 같은 탭 안에서 페이지를 넘길 때 쉬는 간격
_CATALOG_CACHE_TTL_SECONDS = 600

_BADGE_UP = "BT02"
_BADGE_NEW = "BT03"
_ON_ISSUE_PAUSED = "P"
_ADULT_AGE_GRADE = 19

# 실제로 동작이 확인된 브라우저 요청의 헤더를 그대로 따른다(쿠키는 제외). 예전 카카오웹툰
# API에서 진짜 브라우저 헤더(sec-ch-ua*, sec-fetch-*)가 없으면 이따금 HTTP 403이 났던
# 경험이 있어서, 처음부터 갖춰서 보낸다.
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",  # aiohttp가 자동으로 풀어주는 인코딩만(br/zstd는 별도 패키지가 필요)
    "Origin": "https://page.kakao.com",
    "Referer": "https://page.kakao.com/",
    "DNT": "1",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
    ),
    "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"iOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# 이미지 요청용 헤더 — 같은 브라우저 헤더에서 API 전용인 Origin/Accept만 이미지 요청답게 바꾼다
IMAGE_HEADERS = {
    **{k: v for k, v in HEADERS.items() if k != "Origin"},
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
}

_catalog_cache: dict = {"at": 0.0, "items": None}
_catalog_lock = asyncio.Lock()


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: dict, timeout_seconds: int, label: str
) -> dict | None:
    """GET 후 JSON을 돌려준다. 실패하면 None(호출부가 알아서 계속 진행할 수 있게 예외를
    던지지 않는다). HTTP 403/429/5xx는 잠깐 쉬었다가 최대 3번까지 시도한다 — 일시적인
    요청 제한이면 이걸로 풀리는 경우가 있어서."""
    for attempt in range(3):
        try:
            async with session.get(
                url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as response:
                if response.status in (403, 429) or response.status >= 500:
                    if attempt < 2:
                        log.warning(
                            "카카오페이지 조회 실패 (%s): HTTP %s, %d초 뒤 재시도 (%d/3)",
                            label, response.status, 2 * (attempt + 1), attempt + 1,
                        )
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    log.warning("카카오페이지 조회 실패 (%s): HTTP %s (재시도 소진)", label, response.status)
                    return None
                if response.status != 200:
                    log.warning("카카오페이지 조회 실패 (%s): HTTP %s", label, response.status)
                    return None
                return await response.json()
        except Exception as e:
            log.warning("카카오페이지 조회 예외 (%s): %s", label, e)
            return None
    return None


def _split_author_names(authors: str | None) -> list[str]:
    """"A,B, C" → ["A","B","C"] (공백 정리, 중복 제거, 순서 유지)."""
    return list(dict.fromkeys(n.strip() for n in (authors or "").split(",") if n.strip()))


def _thumbnail_url(card: dict) -> str:
    """사이트가 요일연재 카드에 쓰는 이미지 순서 — card_set.background_img가 있으면 그것,
    옛 방식 작품은 card_img, 그것도 없으면 배너 배경."""
    asset = card.get("asset_property") or {}
    kid = (
        (asset.get("card_set") or {}).get("background_img")
        or asset.get("card_img")
        or (asset.get("banner_set") or {}).get("background_img")
    )
    return image_url(kid, IMAGE_FILENAME_CARD) if kid else ""


def image_url(kid: str, filename: str = IMAGE_FILENAME_ORIGINAL) -> str:
    return KAKAO_IMAGE_URL_TMPL.format(kid=kid, filename=filename)


def _card_to_item(card: dict) -> dict | None:
    series_id = card.get("series_id")
    if series_id is None or card.get("category_uid") != WEBTOON_CATEGORY_UID:
        return None
    badge = card.get("badge")
    return {
        "title_id": series_id,
        "title_name": card.get("title", ""),
        "is_adult": (card.get("age_grade") or 0) >= _ADULT_AGE_GRADE,
        "author_names": _split_author_names(card.get("authors")),
        "has_update": badge == _BADGE_UP,
        "is_new": badge == _BADGE_NEW,
        "is_paused": card.get("on_issue") == _ON_ISSUE_PAUSED,
        "thumbnail_url": _thumbnail_url(card),
    }


async def _fetch_weekday_tab(
    session: aiohttp.ClientSession, tab_uid: int, timeout_seconds: int
) -> tuple[list[dict], bool]:
    """요일 탭 하나의 카드 전부(페이지를 is_end까지 넘김). 두 번째 값은 끝까지 성공했는지."""
    cards: list[dict] = []
    for page in range(_MAX_PAGES_PER_TAB):
        # 사이트가 실제로 보내는 형태 그대로 — 첫 페이지엔 bm/subcategory_uid가 없고 이후엔 붙는다
        params = {"category_uid": WEBTOON_CATEGORY_UID, "page": page}
        if page > 0:
            params.update({"bm": "A", "subcategory_uid": 0})
        params.update({"tab_uid": tab_uid, "screen_uid": _SCREEN_UID})

        data = await _get_json(session, KAKAO_DAYOFWEEK_URL, params, timeout_seconds, f"요일 tab_uid={tab_uid} page={page}")
        if data is None:
            return cards, False
        result = data.get("result") or {}
        page_cards = result.get("list") or []
        cards.extend(page_cards)
        if result.get("is_end") or not page_cards:
            return cards, True
        await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)
    log.warning("카카오페이지 요일 목록이 %d페이지를 넘어 중단함 (tab_uid=%s)", _MAX_PAGES_PER_TAB, tab_uid)
    return cards, True


async def _fetch_weekday_catalog_uncached(
    session: aiohttp.ClientSession, timeout_seconds: int
) -> tuple[list[dict], bool]:
    semaphore = asyncio.Semaphore(_TAB_CONCURRENCY)

    async def _one(tab_uid: int):
        async with semaphore:
            return await _fetch_weekday_tab(session, tab_uid, timeout_seconds)

    tab_results = await asyncio.gather(*[_one(t) for t in _WEEKDAY_TAB_UIDS])

    items: dict[int, dict] = {}  # 여러 요일에 걸린 작품("월, 화, 수")은 series_id로 한 번만
    all_ok = True
    for cards, ok in tab_results:
        all_ok = all_ok and ok
        for card in cards:
            item = _card_to_item(card)
            if item is not None:
                items.setdefault(item["title_id"], item)
    return list(items.values()), all_ok


async def fetch_weekday_catalog(
    session: aiohttp.ClientSession, timeout_seconds: int, *, use_cache: bool = True
) -> list[dict]:
    """"웹툰 전체목록"에 보여줄 것 — 요일 7개(지금 연재 중인 것)만 훑는다. 예전(카카오웹툰)엔
    요일당 요청 1번이면 됐지만 지금은 요일당 5~10페이지라 전체 약 60번이 나가서, 결과를
    10분간 메모리에 캐시한다(use_cache=False면 무시하고 새로 받아 캐시도 갱신 — 화면의
    "새로고침"과 다운로드 리포트가 그렇게 쓴다). 일부 요일 조회가 실패했으면 있는 것만
    돌려주되 캐시하지는 않는다(잘못된 부분 결과가 10분간 굳는 걸 막기 위해)."""
    async with _catalog_lock:
        cached = _catalog_cache["items"]
        if use_cache and cached is not None and time.monotonic() - _catalog_cache["at"] < _CATALOG_CACHE_TTL_SECONDS:
            return list(cached)
        items, all_ok = await _fetch_weekday_catalog_uncached(session, timeout_seconds)
        if items and all_ok:
            _catalog_cache["items"] = items
            _catalog_cache["at"] = time.monotonic()
        return list(items)


def product_list_params(series_id: int) -> dict:
    """회차 목록(최신순) 요청 파라미터. 이 응답 한 번으로 최신 회차와 작품의 공식 표지(series_item.
    thumbnail)를 같이 얻는다 — 요일 목록 카드에는 공식 표지가 없어서 작품마다 이 호출이 필요하다."""
    return {"series_id": series_id, "cursor_index": 0, "cursor_direction": "NEXT", "window_size": 25, "sort_type": "desc"}


def thumbnail_kid(product_list_data: dict | None) -> str | None:
    """회차 목록 응답에서 작품의 공식 표지 kid(작품 페이지에 나오는 제목 들어간 표지)를 꺼낸다."""
    series_item = ((product_list_data or {}).get("result") or {}).get("series_item") or {}
    return series_item.get("thumbnail") or None


async def fetch_latest_episode_url(
    session: aiohttp.ClientSession, series_id: int, timeout_seconds: int
) -> str | None:
    """이 작품의 가장 최근 회차로 바로 가는 뷰어 URL을 만든다 — 회차 목록을 최신순
    (sort_type=desc)으로 받아 맨 앞(숨김 처리 안 된) 회차를 쓴다.

    카카오페이지의 "기다리면 무료" 작품은 최신 회차가 전부 아직 안 풀린(is_free: false)
    상태라서, 예전 카카오웹툰처럼 "지금 읽을 수 있는 최신 회차"로 거르면 몇 달 전 회차가
    나와버린다 — UP(새 회차) 표시가 가리키는 건 바로 그 최신 회차라서, 잠겨 있어도
    최신 회차로 보낸다(기다무 이용권/충전이 있으면 그 화면에서 바로 열 수 있다)."""
    data = await _get_json(
        session, KAKAO_PRODUCT_LIST_URL, product_list_params(series_id), timeout_seconds, f"회차 목록 series_id={series_id}"
    )
    if data is None:
        return None
    episodes = [entry.get("item") or {} for entry in (data.get("result") or {}).get("list") or []]
    latest = next((ep for ep in episodes if ep.get("product_id") is not None and not ep.get("hidden")), None)
    if latest is None:
        return None
    return KAKAO_VIEWER_URL_TMPL.format(series_id=series_id, product_id=latest["product_id"])


async def search_by_author(
    session: aiohttp.ClientSession, author_name: str, timeout_seconds: int
) -> list[dict]:
    """작가 이름으로 검색해서, 실제로 그 이름이 저자로 걸린 웹툰만 골라 반환한다. 검색어는
    제목에도 걸릴 수 있고 웹소설/책도 섞여 나오므로, 웹툰 카테고리이면서 authors 목록에
    그 이름이 정확히 있는 것만 남긴다(완결작 포함 — 검색은 완결 여부로 거르지 않는다).
    반환은 title_id/title_name/is_adult만 담은 dict 리스트."""
    results: list[dict] = []
    for page in range(_MAX_SEARCH_PAGES):
        data = await _get_json(
            session,
            KAKAO_SEARCH_URL,
            {
                "keyword": author_name, "category_uid": WEBTOON_CATEGORY_UID, "is_complete": "false",
                "sort_type": "ACCURACY", "page": page, "size": 25,
            },
            timeout_seconds,
            f"검색 author={author_name} page={page}",
        )
        if data is None:
            break
        result = data.get("result") or {}
        entries = result.get("list") or []
        for item in entries:
            if item.get("category_uid") != WEBTOON_CATEGORY_UID or item.get("series_id") is None:
                continue
            if author_name not in _split_author_names(item.get("authors")):
                continue
            results.append(
                {
                    "title_id": item["series_id"],
                    "title_name": item.get("title", ""),
                    "is_adult": (item.get("age_grade") or 0) >= _ADULT_AGE_GRADE,
                }
            )
        if result.get("is_end") or not entries:
            break
        await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)
    return results


def extract_candidate_author_names(items: list[dict]) -> list[str]:
    """요일별 목록에서 저자 이름을 중복 없이 모은다("작가/태그 관리"의 관심 작가 후보). 카카오
    페이지 목록은 저자가 글/그림/원작 구분 없이 이름만 나와서, 삽화가나 스튜디오 이름도
    후보에 섞인다."""
    names: set[str] = set()
    for item in items:
        names.update(item.get("author_names") or [])
    return sorted(names)
