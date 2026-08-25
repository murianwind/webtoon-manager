"""
comic.naver.com 내부 API 호출 전담 모듈.

원칙: raw dict 파싱은 이 파일에서만 한다. 다른 모듈(downloader/tracker/comicinfo)은
여기서 반환하는 TitleInfo/EpisodeInfo 같은 정리된 객체만 사용한다 (SRP).
"""

import asyncio
import logging
import re
from typing import Optional

import aiohttp

from app.constants import (
    DEFAULT_HEADERS,
    EPISODE_LIST_MAX_RETRIES,
    NAVER_ARTIST_OTHER_TITLES_URL,
    NAVER_CURATION_LIST_URL,
    NAVER_CURATION_META_URL,
    NAVER_INFO_URL,
    NAVER_LIST_URL,
    NAVER_WEEKDAY_LIST_URL,
    RETRY_BACKOFF_BASE_SECONDS,
)
from app.models import EpisodeInfo, NaverListItem, SearchResultItem, TitleInfo

log = logging.getLogger(__name__)

_WEBTOON_CODE_TO_TYPE = {
    "WEBTOON": "webtoon",
    "CHALLENGE": "challenge",
    "BEST_CHALLENGE": "bestChallenge",
}


class NaverApiError(Exception):
    """네이버 API 요청이 최종적으로 실패했을 때."""


def _parse_title_info(raw: dict, title_id: str) -> TitleInfo:
    age = raw.get("age") or {}
    author = raw.get("author") or {}
    gfp = raw.get("gfpAdCustomParam") or {}

    writer_entries = author.get("writers") or []
    painter_entries = author.get("painters") or []

    # communityArtists는 없을 수도 있는 확장 필드라 안전하게 조회
    writer_ids: set[str] = set()
    for artist in raw.get("communityArtists") or []:
        artist_types = artist.get("artistTypeList") or []
        if "ARTIST_WRITER" in artist_types and artist.get("artistId") is not None:
            writer_ids.add(str(artist["artistId"]))
    # communityArtists가 없는 응답 대비: author.writers의 id도 보조로 채운다
    if not writer_ids:
        writer_ids = {str(w["id"]) for w in writer_entries if w.get("id")}

    writer_id_name_pairs = [
        (str(w["id"]), w.get("name", "")) for w in writer_entries if w.get("id")
    ]

    return TitleInfo(
        title_id=title_id,
        title_name=raw.get("titleName", ""),
        synopsis=raw.get("synopsis", ""),
        is_adult=(age.get("type") == "RATE_18"),
        webtoon_type=_WEBTOON_CODE_TO_TYPE.get(raw.get("webtoonLevelCode", ""), "webtoon"),
        is_finished=bool(raw.get("finished")),
        thumbnail_url=raw.get("thumbnailUrl", ""),
        writer_names=[w.get("name", "") for w in writer_entries if w.get("name")],
        painter_names=[p.get("name", "") for p in painter_entries if p.get("name")],
        writer_ids=writer_ids,
        writer_id_name_pairs=writer_id_name_pairs,
        genres=list(gfp.get("genreTypes") or []),
        tags=list(gfp.get("tags") or []),
        age_description=age.get("description", ""),
        # 'rest'는 네이버 응답에서 확인되지 않은 필드일 수 있어 best-effort로 파싱한다.
        is_paused=bool(raw.get("rest", False)),
    )


async def fetch_title_info(
    session: aiohttp.ClientSession, title_id: str, timeout_seconds: int
) -> Optional[TitleInfo]:
    try:
        async with session.get(
            NAVER_INFO_URL,
            params={"titleId": title_id},
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                log.error("title info 요청 실패 (titleId=%s): HTTP %s", title_id, response.status)
                return None
            raw = await response.json()
            return _parse_title_info(raw, title_id)
    except Exception as e:
        log.error("title info 요청 중 예외 (titleId=%s): %s", title_id, e)
        return None


async def _fetch_episode_list_page(
    session: aiohttp.ClientSession,
    title_id: str,
    page: int,
    cookies: dict[str, str],
    timeout_seconds: int,
) -> Optional[dict]:
    last_error: Optional[Exception] = None
    for attempt in range(EPISODE_LIST_MAX_RETRIES + 1):
        try:
            async with session.get(
                NAVER_LIST_URL,
                params={"titleId": title_id, "page": page},
                headers=DEFAULT_HEADERS,
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status == 200:
                    return await response.json()
                last_error = NaverApiError(f"HTTP {response.status}")
        except Exception as e:
            last_error = e

        if attempt < EPISODE_LIST_MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF_BASE_SECONDS * (2**attempt))

    log.error(
        "episode list 페이지 요청 최종 실패 (titleId=%s, page=%s): %s", title_id, page, last_error
    )
    return None


async def fetch_all_episodes(
    session: aiohttp.ClientSession,
    title_id: str,
    cookies: dict[str, str],
    timeout_seconds: int,
) -> list[EpisodeInfo]:
    """
    전체 회차를 no 오름차순으로 반환한다. list API 자체에 접근 불가(성인+미인증 등)면
    빈 리스트를 반환하며, 이 경우 상위 로직에서 "다운로드 가능한 회차 없음"으로 처리된다.
    """
    first_page = await _fetch_episode_list_page(session, title_id, 1, cookies, timeout_seconds)
    if not first_page:
        return []

    total_pages = (first_page.get("pageInfo") or {}).get("totalPages", 1) or 1

    pages_raw = [first_page]
    if total_pages > 1:
        tasks = [
            _fetch_episode_list_page(session, title_id, page, cookies, timeout_seconds)
            for page in range(2, total_pages + 1)
        ]
        pages_raw.extend(await asyncio.gather(*tasks))

    episodes: list[EpisodeInfo] = []
    for page_data in pages_raw:
        if not page_data:
            continue
        for article in page_data.get("articleList") or []:
            episodes.append(
                EpisodeInfo(
                    episode_no=article.get("no", 0),
                    subtitle=article.get("subtitle", ""),
                    is_locked=bool(article.get("thumbnailLock")),
                )
            )

    episodes.sort(key=lambda ep: ep.episode_no)
    return episodes


def free_episodes_only(episodes: list[EpisodeInfo]) -> list[EpisodeInfo]:
    """썸네일 잠금(유료/미공개)이 걸린 첫 회차를 만나면 그 이후는 제외한다."""
    free: list[EpisodeInfo] = []
    for episode in episodes:
        if episode.is_locked:
            break
        free.append(episode)
    return free


async def fetch_full_webtoon_list(
    session: aiohttp.ClientSession, timeout_seconds: int
) -> list[NaverListItem]:
    """
    네이버 '요일전체' 탭과 동일한 API를 한 번 호출해서 전체 목록을 가져온다.

    실제 응답 형태(HAR 캡처로 확인): GET /api/webtoon/titlelist/weekday?order=user
    -> {"titleListMap": {"MONDAY": [...], "TUESDAY": [...], ...}}
    각 항목의 up=새 에피소드 업데이트(UP 아이콘), rest=휴재, finish=완결, adult=성인.
    """
    try:
        async with session.get(
            NAVER_WEEKDAY_LIST_URL,
            params={"order": "user"},
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                raise NaverApiError(f"요일별 전체 목록 조회 실패: HTTP {response.status}")
            data = await response.json()
    except NaverApiError:
        raise
    except Exception as e:
        raise NaverApiError(f"요일별 전체 목록 조회 예외: {e}") from e

    title_list_map = data.get("titleListMap") or {}
    merged: dict[str, NaverListItem] = {}

    for weekday, items in title_list_map.items():
        for item in items:
            title_id = str(item.get("titleId", ""))
            if not title_id:
                continue

            existing = merged.get(title_id)
            if existing:
                if weekday not in existing.weekdays:
                    existing.weekdays.append(weekday)
                continue

            merged[title_id] = NaverListItem(
                title_id=title_id,
                title_name=item.get("titleName", ""),
                thumbnail_url=item.get("thumbnailUrl", ""),
                weekdays=[weekday],
                is_finished=bool(item.get("finish")),
                is_paused=bool(item.get("rest")),
                has_update=bool(item.get("up")),
                is_adult=bool(item.get("adult")),
                author_summary=item.get("author", ""),
            )

    return sorted(merged.values(), key=lambda x: x.title_name)


async def fetch_tag_catalog(
    session: aiohttp.ClientSession, timeout_seconds: int
) -> list[dict]:
    """네이버가 제공하는 전체 태그(큐레이션) 카탈로그. [{"tag_id": "134", "tag_name": "먼치킨"}, ...]"""
    try:
        async with session.get(
            NAVER_TAG_SHORTCUT_URL,
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                raise NaverApiError(f"태그 카탈로그 조회 실패: HTTP {response.status}")
            data = await response.json()
    except NaverApiError:
        raise
    except Exception as e:
        raise NaverApiError(f"태그 카탈로그 조회 예외: {e}") from e

    return [
        {"tag_id": str(item["id"]), "tag_name": item.get("text", item.get("name", ""))}
        for item in data.get("tagItemList") or []
        if item.get("type") == "CUSTOM_TAG" and item.get("id") is not None
    ]


async def search_webtoons(
    session: aiohttp.ClientSession, keyword: str, timeout_seconds: int
) -> list[SearchResultItem]:
    """
    네이버 통합검색. 제목뿐 아니라 작가 이름으로도 매칭되고, 요일별 목록 API와 달리
    장기 휴재작도 검색 결과에 나온다 (weekday API의 한계를 우회할 수 있는 유일한 경로).

    응답에 여러 카테고리(searchWebtoonResult, searchNbooksComicResult 등)가 섞여 있는데,
    실제 titleId를 담고 있는 카테고리만(예: searchNbooksComicResult는 titleId가 아니라
    별개의 contentId라 여기서 걸러진다) 골라서 파싱한다.
    """
    try:
        async with session.get(
            NAVER_SEARCH_ALL_URL,
            params={"keyword": keyword},
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                log.error("통합검색 실패 (keyword=%s): HTTP %s", keyword, response.status)
                return []
            data = await response.json()
    except Exception as e:
        log.error("통합검색 예외 (keyword=%s): %s", keyword, e)
        return []

    results: list[SearchResultItem] = []
    seen_ids: set[str] = set()

    for category in data.values():
        if not isinstance(category, dict):
            continue
        for item in category.get("searchViewList") or []:
            title_id = item.get("titleId")
            if title_id is None:  # contentId만 있는(titleId 없는) 카테고리는 건너뜀
                continue
            title_id = str(title_id)
            if title_id in seen_ids:
                continue
            seen_ids.add(title_id)

            authors = [
                (str(a["artistId"]), a.get("name", ""))
                for a in item.get("communityArtists") or []
                if a.get("artistId") is not None
            ]
            genres = [g.get("description", "") for g in item.get("genreList") or [] if g.get("description")]
            tags = [t.get("tagName", "") for t in item.get("tagList") or [] if t.get("tagName")]

            results.append(
                SearchResultItem(
                    title_id=title_id,
                    title_name=item.get("titleName", ""),
                    thumbnail_url=item.get("thumbnailUrl", ""),
                    is_finished=bool(item.get("finished")),
                    is_paused=bool(item.get("rest")),
                    is_adult=bool(item.get("adult")),
                    has_update=bool(item.get("up")),
                    author_ids_names=authors,
                    genres=genres,
                    tags=tags,
                )
            )

    return results


def extract_candidate_author_names(items: list[NaverListItem]) -> list[str]:
    """
    작가 이름 전체 목록을 제공하는 네이버 API가 없어서, 대신 요일별 전체목록의
    저자 텍스트("박만사, 남자의 이야기 / 정종택" 같은 조합)를 흩어서 이름 후보를
    뽑아낸다. 정확한 author_id는 아니라 "둘러보기용 후보" 성격이고, 실제 등록은
    이 이름으로 검색(search_authors_by_name)해서 얻은 id로 한다.
    """
    names: set[str] = set()
    for item in items:
        for part in re.split(r"[/,·]", item.author_summary):
            name = part.strip()
            if name:
                names.add(name)
    return sorted(names)


async def fetch_other_titles_by_artist(
    session: aiohttp.ClientSession, title_id: str, timeout_seconds: int
) -> list[dict]:
    try:
        async with session.get(
            NAVER_ARTIST_OTHER_TITLES_URL,
            params={"titleId": title_id},
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                return []
            return await response.json()
    except Exception as e:
        log.error("작가의 다른 작품 조회 실패 (titleId=%s): %s", title_id, e)
        return []


async def fetch_curation_title_name(
    session: aiohttp.ClientSession, tag_id: int, timeout_seconds: int
) -> str:
    try:
        async with session.get(
            NAVER_CURATION_META_URL,
            params={"type": "CUSTOM_TAG", "id": tag_id},
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("curationTitle", str(tag_id))
    except Exception as e:
        log.error("curation/meta 조회 실패 (tag_id=%s): %s", tag_id, e)
    return str(tag_id)


async def fetch_curation_titles(
    session: aiohttp.ClientSession, tag_id: int, timeout_seconds: int, delay_seconds: float
) -> list[dict]:
    all_items: list[dict] = []
    page = 1
    while True:
        try:
            async with session.get(
                NAVER_CURATION_LIST_URL,
                params={"type": "CUSTOM_TAG", "id": tag_id, "page": page, "pageSize": 100, "order": "USER"},
                headers=DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status != 200:
                    log.error("curation/list 조회 실패 (tag_id=%s, page=%s): HTTP %s", tag_id, page, response.status)
                    break
                data = await response.json()
        except Exception as e:
            log.error("curation/list 예외 (tag_id=%s, page=%s): %s", tag_id, page, e)
            break

        all_items.extend(data.get("curationViewList") or [])
        page_info = data.get("pageInfo") or {}
        if page >= page_info.get("totalPages", page):
            break
        page += 1
        await asyncio.sleep(delay_seconds)

    return all_items
