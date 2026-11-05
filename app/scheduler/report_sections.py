"""리포트에 담는 "등록 안 한 작품의 새 에피소드"(네이버, 카카오 순) 수집 — 다운로드 기록과는 따로 확인한다.
"""

import asyncio
import logging
import re


import aiohttp

from app import daily_plus, job_status, kakao_api, kakao_page_auth, naver_api, report_seen, repository


from app.cookie_loader import get_adult_cookies


log = logging.getLogger(__name__)

_UNREGISTERED_NEW_EPISODE_LIMIT = 20  # 미등록 신규 에피소드는 네이버에 회차번호를 하나씩 더 물어봐야 해서, 너무 많이 걸면 부담이라 따로 더 작게 제한


# 카카오는 "웹툰 전체목록"에 있는(=요일 7개, 제외 안 한) 것 전부가 후보 대상이라
# 네이버의 "완전 미등록"보다 모수가 훨씬 크다 — UP 표시된 게 20개를 넘는 날은 실제로
# 흔해서, 위 한도를 그대로 쓰면 뒤로 밀린 작품(카탈로그에서 나중에 나온 것)의 새
# 에피소드가 후보에도 못 들고 조용히 빠지는 문제가 있었다(실제로 확인됨: "바퀴벌레
# 잔혹사"가 UP인데 리포트엔 안 나옴). 조회 자체(뷰어/카카오 최신 회차 확인)는 이미
# 동시 실행 수 제한(artist_scan_concurrency)이 있어서 이 한도를 넉넉히 키워도
# 부담이 크지 않고, 실제 디스코드 메시지에 보여줄 개수는 report_message.build_report_message
# 에서 REPORT_LIST_LIMIT로 별도로 줄인다(둘을 분리해야 "후보에서 누락"과 "메시지에서
# 생략 표시하며 자름"이 다른 문제라는 게 명확해진다).
_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT = 150
_NEW_TITLE_LIMIT = 50  # 한 번에 알리는 신규 웹툰 수(신작은 하루에 많아야 몇 개라 넉넉하다)
_AUTHOR_SPLIT_RE = re.compile(r"[,/·]")


def _registered_author_names(platform: str) -> set[str]:
    """등록해서 사용 중인(켜 둔) 작가의 이름 — 이 작가의 신작은 작가 알림이 따로 알려서 신규 웹툰 알림에서는 뺀다."""
    return {author.author_name for author in repository.list_watched_authors(platform) if author.enabled and author.author_name}


def _by_registered_author(names: list[str], registered: set[str]) -> bool:
    return any(name.strip() in registered for name in names)


def _is_new_not_yet_announced(platform: str, title_id, is_new: bool) -> bool:
    """사이트의 신작 표시가 있는데 아직 신규 웹툰으로 알리지 않은 작품 — 새 에피소드가 아니라 신규 웹툰 섹션이 맡는다."""
    return is_new and not report_seen.is_new_announced(platform, title_id)


def _log_unchecked_adult(platform_label: str, titles: list[str], how_to_fix: str) -> None:
    """성인 작품의 새 회차를 쿠키 문제로 확인하지 못했을 때 조용히 넘기지 않고 리포트 실행 로그에 남긴다(리포트 메시지에는 넣지 않는다)."""
    if not titles:
        return
    shown = ", ".join(titles[:5]) + (" 등" if len(titles) > 5 else "")
    message = f"{platform_label} 성인 작품 {len(titles)}개는 새 에피소드를 확인하지 못했습니다({shown}) — {how_to_fix}"
    log.warning(message)
    job_status.log_line("report", message)


async def collect_unregistered_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[str, str, int]]:
    """네이버 "웹툰 전체목록"에 보이는 **미구독** 작품 중 새 에피소드(UP 표시)가 있는 것만 골라서
    (title_id, title_name, 최신 회차 번호)로 반환한다(카카오 _collect_kakao_new_episodes와 같은 규칙).
    미구독 = 기록이 없거나, 구독한 적이 있지만 "목록으로" 되돌려 "목록" 상태인 작품. 구독 중인 작품은
    다운로드로 알리고, 구독해제/제외됨은 "안 챙겨본다"는 뜻이라 알리지 않는다. 구독을 안 해서 놓치고
    있었을 수도 있는 신작을 리포트에서 바로 발견하게 해 주는 용도라, 없는 게 정상인 경우가 대부분이고
    실패해도 리포트 자체는 계속 보내야 한다."""
    try:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except Exception as e:
        log.error("미등록 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    def _not_subscribed_in_list(title_id: str) -> bool:
        record = repository.get(title_id)
        return record is None or record.status == repository.STATUS_UNREGISTERED

    hidden = await daily_plus.current_ids(settings.request_timeout_seconds, session)  # 매일+ 작품은 화면에서 숨기므로 알리지도 않는다(목록을 모르면 거르지 않음)
    candidates = [
        item for item in items
        if item.has_update and item.title_id not in hidden and _not_subscribed_in_list(item.title_id)
        and not _is_new_not_yet_announced("naver", item.title_id, item.is_new)  # 신규로 알리기 전의 신작은 신규 웹툰 섹션이 맡는다
    ]
    candidates = candidates[:_UNREGISTERED_NEW_EPISODE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 성인 작품은 성인 인증 쿠키가 없으면 회차 목록이 비어서 와서 최신 회차를 알 수 없다 — 다운로드와 같은 쿠키로 조회한다
    adult_cookies = get_adult_cookies(settings.cookie_file_path)

    async def _fetch_one(item):
        async with semaphore:
            try:
                episode_no = await naver_api.fetch_latest_episode_no(
                    session, item.title_id, settings.request_timeout_seconds, cookies=adult_cookies if item.is_adult else None,
                )
            except Exception as e:
                log.error("미등록 작품(titleId=%s) 최신 회차 조회 실패(건너뜀): %s", item.title_id, e)
                episode_no = None
            await asyncio.sleep(settings.delay_seconds)
            return item, episode_no

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "네이버", [item.title_name for item, episode_no in results if episode_no is None and item.is_adult],
        "성인 인증 쿠키 파일(COOKIE_DIR_HOST_PATH)이 없거나 만료됐을 수 있습니다",
    )
    return [
        (item.title_id, item.title_name, episode_no)
        for item, episode_no in results
        if episode_no is not None
    ]


async def collect_kakao_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[int, str, str]]:
    """네이버의 _collect_unregistered_new_episodes와 같은 취지로, 카카오페이지 "웹툰 전체목록"에 있는 **미구독** 웹툰 중 새 회차(UP
    표시)가 있는 것만 골라 (title_id, title_name, 카카오페이지 바로가기 URL)로 반환한다. 구독을 안 해서 놓치고 있던 신작을 리포트에서
    발견하게 해 주는 용도다.

    구독 중인 작품은 이 앱이 직접 받아서 "받은 작품"에 나오므로 여기서는 뺀다(예전엔 카카오 "구독"이 웹툰 뷰어에 있다는 표시일 뿐이라
    구독 중인 작품도 알려 줬지만, 지금은 중복 알림이다). 구독해제/제외됨도 "더 이상 안 챙겨본다"는 뜻이라 뺀다. 목록에 그대로 보이는
    "목록" 상태와 아직 아무 기록이 없는 작품만 후보로 남는다. 구독하지 않은 작품은 웹툰 뷰어에 없으니 링크는 항상 카카오페이지 것이다."""
    try:
        # 리포트는 화면 캐시(10분)가 아니라 그 시점의 최신 목록으로 만든다
        items = await kakao_api.fetch_weekday_catalog(session, settings.request_timeout_seconds, use_cache=False)
    except Exception as e:
        log.error("카카오 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    skipped_ids = (
        repository.get_kakao_excluded_title_ids()
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_UNSUBSCRIBED)}
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)}
    )
    candidates = [
        item for item in items
        if item["has_update"] and item["title_id"] not in skipped_ids
        and not _is_new_not_yet_announced("kakao", item["title_id"], item["is_new"])
    ]
    candidates = candidates[:_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 19세 작품은 로그인한 계정으로 조회한다(네이버 성인 작품과 같은 규칙) — 저장된 카카오페이지 로그인 쿠키가 없으면 로그인 없이 시도
    saved = kakao_page_auth.load_cookies()
    login_cookies = kakao_page_auth.cookie_map(saved) if saved else None

    async def _fetch_one(item):
        async with semaphore:
            try:
                url = await kakao_api.fetch_latest_episode_url(
                    session, item["title_id"], settings.request_timeout_seconds, cookies=login_cookies if item["is_adult"] else None,
                )
            except Exception as e:
                log.error("카카오 작품(title_id=%s) 최신 회차 조회 실패(건너뜀): %s", item["title_id"], e)
                url = None
            await asyncio.sleep(settings.delay_seconds)
            return item, url

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "카카오", [item["title_name"] for item, url in results if url is None and item["is_adult"]],
        "카카오페이지 로그인 쿠키가 없거나 만료됐을 수 있습니다(설정 > 카카오웹툰 관리)",
    )
    final = [(item["title_id"], item["title_name"], url) for item, url in results if url is not None]
    if len(final) < len(candidates):
        # 후보였는데 최종 리포트엔 안 실린 개수 — 조회 실패(HTTP 403 등)로 조용히
        # 빠진 게 몇 개인지 여기서 한눈에 보이게 남겨둔다. 원인 자체는 위
        # fetch_latest_episode_url/조회 실패 경고 로그에 있지만, 매번 그걸 하나하나
        # 찾아보지 않아도 "얼마나 빠졌는지"는 바로 알 수 있어야 한다.
        log.warning(
            "카카오 새 에피소드 후보 %d개 중 %d개만 최종 리포트에 포함됨(나머지는 회차 조회 실패)",
            len(candidates), len(final),
        )
    return final


async def collect_new_episode_sections(session: aiohttp.ClientSession, settings, *, force_test: bool) -> tuple[list, list]:
    """등록 안 한 작품의 새 에피소드(네이버, 카카오 순). 다운로드 기록과 무관하게(rows가 비어 있어도) 항상 확인한다 — 구독을 안 해서
    기록 자체가 없는 작품을 발견하는 게 이 섹션의 목적이라, "받은 게 없으니 리포트도 없음"에 묻히면 안 된다. 일반 발송에서는 이미
    같은 회차로 알린 작품을 뺀다(UP 표시가 남아 있는 동안 리포트마다 반복되지 않게) — 테스트 발송은 전부 보여준다."""
    enabled = repository.get_setting("report_unregistered_new_episodes_enabled") != "0"
    kakao_enabled = repository.get_setting("kakao_webtoons_enabled") == "1"
    naver_items = await collect_unregistered_new_episodes(session, settings) if enabled else []
    kakao_items = await collect_kakao_new_episodes(session, settings) if enabled and kakao_enabled else []
    if not force_test:
        naver_items, kakao_items = report_seen.filter_unseen(naver_items, kakao_items)
    return naver_items, kakao_items


async def collect_naver_new_titles(session: aiohttp.ClientSession, settings) -> list[report_seen.NewNaver]:
    """네이버 "웹툰 전체목록"의 신작(신규 표시) 중 미구독(기록이 없거나 "목록" 상태)·매일+ 아님·등록한 작가의 작품이 아닌 것 — (title_id, title_name). 알린 적 없는지는 호출하는 쪽에서 거른다.
    실패해도 리포트는 계속 보내야 해서 목록을 못 받으면 빈 목록이다."""
    try:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except Exception as e:
        log.error("신규 웹툰 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []
    hidden = await daily_plus.current_ids(settings.request_timeout_seconds, session)
    registered = _registered_author_names("naver")

    def _not_subscribed_in_list(title_id: str) -> bool:
        record = repository.get(title_id)
        return record is None or record.status == repository.STATUS_UNREGISTERED

    return [
        (item.title_id, item.title_name) for item in items
        if item.is_new and item.title_id not in hidden and _not_subscribed_in_list(item.title_id)
        and not _by_registered_author(_AUTHOR_SPLIT_RE.split(item.author_summary or ""), registered)
    ][:_NEW_TITLE_LIMIT]


async def collect_kakao_new_titles(session: aiohttp.ClientSession, settings) -> list[report_seen.NewKakao]:
    """카카오페이지 전체목록의 신작 중 구독 중/구독해제/제외됨이 아니고 등록한 작가의 작품이 아닌 것 — (title_id, title_name)."""
    try:
        items = await kakao_api.fetch_weekday_catalog(session, settings.request_timeout_seconds, use_cache=False)
    except Exception as e:
        log.error("카카오 신규 웹툰 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []
    skipped_ids = (
        repository.get_kakao_excluded_title_ids()
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_UNSUBSCRIBED)}
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)}
    )
    registered = _registered_author_names("kakao")
    return [
        (item["title_id"], item["title_name"]) for item in items
        if item["is_new"] and item["title_id"] not in skipped_ids and not _by_registered_author(item["author_names"], registered)
    ][:_NEW_TITLE_LIMIT]


async def collect_new_title_section(session: aiohttp.ClientSession, settings, *, force_test: bool) -> tuple[list[report_seen.NewNaver], list[report_seen.NewKakao]]:
    """신규 웹툰 섹션(네이버, 카카오 순). 일반 발송에서는 이미 신규로 알린 작품을 뺀다 — 테스트 발송은 전부 보여준다."""
    enabled = repository.get_setting("report_unregistered_new_episodes_enabled") != "0"
    kakao_enabled = repository.get_setting("kakao_webtoons_enabled") == "1"
    naver_items = await collect_naver_new_titles(session, settings) if enabled else []
    kakao_items = await collect_kakao_new_titles(session, settings) if enabled and kakao_enabled else []
    if not force_test:
        naver_items, kakao_items = report_seen.filter_unannounced_new(naver_items, kakao_items)
    return naver_items, kakao_items

