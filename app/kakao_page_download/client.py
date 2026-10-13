"""카카오페이지 HTTP 클라이언트(쿠키, 재시도, 회차 목록, 이용권, 이미지) — 서버와 주고받는 부분.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
from yarl import URL

from app import kakao_api, kakao_page_auth

from app.kakao_page_download.models import (
    ABOUT_URL,
    Episode,
    PRODUCT_LIST_URL,
    PROFILE_URL,
    RENTAL_TICKET_TYPE,
    TICKET_MY_URL,
    TICKET_READY_URL,
    TICKET_USE_URL,
    TicketInfo,
    VIEWER_DATA_URL,
    WAITFREE_TICKET_TYPE,
    _IMAGE_RETRIES,
    _KST,
    _LIST_WINDOW_SIZE,
    _MAX_LIST_PAGES,
    _PAGE_INTERVAL_SECONDS,
    _parse_episode,
    is_excluded_item,
)

log = logging.getLogger(__name__)

# ── 클라이언트 ────────────────────────────────────────────────────────────

class KakaoPageClient:
    """로그인 쿠키를 붙여 카카오페이지 API/이미지를 부르는 클라이언트. 세션은 쿠키 저장소가 없는
    것(aiohttp.DummyCookieJar)으로 만들어 쓴다 — 쿠키는 이 객체가 직접 관리하고, 서버가 새로 내려준
    쿠키는 cookies에 반영하며 cookies_changed로 표시한다(호출부가 끝나고 저장)."""

    def __init__(self, session: aiohttp.ClientSession, cookies: dict[str, str], timeout_seconds: int):
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.cookies = dict(cookies)
        self.cookies_changed = False
        self.last_failure: str | None = None  # 가장 최근 받기 실패의 원인(화면 로그 한 줄에 붙인다). 새 회차를 받기 시작할 때 지운다

    def _headers(self, *, image: bool = False, post: bool = False) -> dict:
        headers = dict(kakao_api.IMAGE_HEADERS if image else kakao_api.HEADERS)
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return headers

    def _absorb_cookies(self, response) -> None:
        for name, morsel in response.cookies.items():
            if morsel.value and self.cookies.get(name) != morsel.value:
                self.cookies[name] = morsel.value
                self.cookies_changed = True

    async def _request_json(self, method: str, url: str, *, params: dict | None = None, label: str, data: dict | None = None):
        """(HTTP 상태, JSON 또는 None). 요청 자체가 실패하면 (None, None)."""
        try:
            if method == "POST":
                request = self._session.post(url, data=data or {}, headers=self._headers(post=True), timeout=self._timeout)
            else:
                request = self._session.get(url, params=params, headers=self._headers(), timeout=self._timeout)
            async with request as response:
                self._absorb_cookies(response)
                data = await response.json(content_type=None) if response.status == 200 else None
                return response.status, data
        except Exception as e:
            log.warning("카카오페이지 요청 실패 (%s): %s", label, e)
            return None, None

    async def check_login(self) -> bool | None:
        """True=로그인됨, False=로그인 풀림, None=모름(네트워크 문제/차단/서버 오류 — 로그아웃으로 단정하지 않는다)."""
        status, data = await self._request_json("POST", PROFILE_URL, label="로그인 확인")
        if status is None:
            return None
        if status == 401:
            return False
        if status != 200:
            return None
        return bool(data and data.get("result_code") == 0 and (data.get("profile") or {}).get("uid"))

    async def list_episodes(self, series_id: int) -> tuple[dict, list[Episode]] | None:
        """(작품 정보, 회차 목록(번호 오름차순))를 돌려준다. 중간에 하나라도 실패하면 None — 일부만 본 목록으로
        받을 회차를 정하면 안 되기 때문이다."""
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        series_item: dict = {}
        episodes: dict[int, Episode] = {}
        excluded_orders: list[int] = []  # 동영상/체험판 회차의 순서 번호(뒤 회차의 번호를 그만큼 당기는 데 쓴다)
        cursor, direction = 0, "NEXT"
        for _ in range(_MAX_LIST_PAGES):
            params = {
                "series_id": series_id, "cursor_index": cursor, "cursor_direction": direction, "window_size": _LIST_WINDOW_SIZE,
                "sort_type": "asc",  # 계정에 저장된 정렬과 무관하게 항상 첫화부터
            }
            _, data = await self._request_json("GET", PRODUCT_LIST_URL, params=params, label=f"회차 목록 series_id={series_id}")
            if data is None:
                return None
            result = data.get("result") or {}
            if not series_item:
                series_item = result.get("series_item") or {}
            entries = result.get("list") or []
            new_count = 0
            for entry in entries:
                item = entry.get("item") or {}
                product_id = item.get("product_id")
                if product_id is None or product_id in episodes:
                    continue
                if is_excluded_item(item):
                    excluded_orders.append(int(item.get("order_value") or 0))
                    episodes[product_id] = None  # 다시 세지 않게 표시만 해 둔다
                else:
                    episodes[product_id] = _parse_episode(item, series_item.get("title", ""), now_kst)
                new_count += 1
            cursor = entries[-1].get("cursor_index") if entries else None
            if not result.get("has_next") or new_count == 0 or cursor is None:
                break
            await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
        series_item["_excluded_video_count"] = len(excluded_orders)  # 동영상/체험판이라 목록에서 뺀 회차 수(분석 화면의 "사이트 회차 수" 비교용)
        images = [e for e in episodes.values() if e is not None and e.number > 0]
        for episode in images:
            episode.number -= sum(1 for order in excluded_orders if order < episode.number)
        return series_item, sorted(images, key=lambda e: e.number)

    async def ticket_info(self, series_id: int) -> TicketInfo | None:
        """이 작품의 대여권/소장권 수와 기다무 상태. 못 받으면 None(기다무를 못 쓰는 것으로 다룬다)."""
        _, data = await self._request_json(
            "GET", TICKET_MY_URL, params={"series_id": series_id, "include_waitfree": "true"}, label=f"이용권 series_id={series_id}"
        )
        if not data or data.get("result_code") != 0:
            return None
        result = data.get("result") or {}
        mine, waitfree = result.get("my") or {}, result.get("waitfree") or {}
        ready = bool(waitfree.get("charged_complete"))
        return TicketInfo(
            rental_count=int(mine.get("ticket_rental_count") or 0), own_count=int(mine.get("ticket_own_count") or 0),
            waitfree_ready=ready, waitfree_available_at=None if ready else waitfree.get("charged_at"),
            waitfree_period_minutes=int(waitfree.get("charged_period_by_minute") or 0),
        )

    async def _ready_to_use(self, product_id: int, label: str) -> dict | None:
        """회차 하나의 이용권 사용 가능 정보(ticket/ready_to_use의 result). 못 받았거나 result_code가 0이 아니면 None."""
        _, ready = await self._request_json("GET", TICKET_READY_URL, params={"product_id": product_id, "include_series": "true"}, label=f"{label} product_id={product_id}")
        if not ready or ready.get("result_code") != 0:
            return None
        return ready.get("result") or {}

    async def use_waitfree_ticket(self, product_id: int) -> tuple[bool, str]:
        """회차 하나를 기다무 대여권으로 연다. (성공 여부, 사람이 읽을 이유). 이 회차에 실제로 쓸 수 있는 이용권이
        기다무(RT05)라고 서버가 알려줄 때만 쓴다 — 선물권 등 다른 이용권이나 결제가 필요한 경우는 쓰지 않는다."""
        result = await self._ready_to_use(product_id, "이용권 확인")
        if result is None:
            return False, "이용권 상태를 확인하지 못했습니다"
        if (result.get("single") or {}).get("waitfree_block"):
            return False, "기다무로 열 수 없는 회차입니다"
        ticket_type = ((result.get("available") or {}).get("ticket_rental_type"))
        if ticket_type != WAITFREE_TICKET_TYPE:
            return False, "기다무를 쓸 수 없습니다" + (f"(가능한 이용권: {ticket_type})" if ticket_type else "")
        status, used = await self._request_json(
            "POST", TICKET_USE_URL, data={"product_id": product_id, "ticket_type": WAITFREE_TICKET_TYPE}, label=f"기다무 사용 product_id={product_id}"
        )
        if not used or used.get("result_code") != 0:
            return False, f"기다무 사용에 실패했습니다(HTTP {status})"
        return True, "기다무로 열었습니다"

    async def use_rental_ticket(self, product_id: int) -> tuple[bool, str, int | None]:
        """회차 하나를 일반 대여권(RT01)으로 연다. (성공 여부, 사람이 읽을 이유, 이 작품에 남은 대여권 수 또는 None).
        서버가 이 회차에 쓸 수 있는 이용권이 RT01이라고 알려 주고, 보유 대여권이 1장 이상이고, 기다무로 열 수 없는 회차(기다무가 없는 작품이거나
        waitfree_block)일 때만 쓴다 — 기다무로 열 수 있는 회차에 대여권을 쓰는 낭비와 선물권/결제를 막는다. 사용 요청은 **한 번만** 보낸다:
        응답을 못 받았으면 서버가 처리했는지 다시 조회해서(대여 중이 됐는지) 판단하고, 절대 다시 보내지 않는다(이중 소모 방지)."""
        result = await self._ready_to_use(product_id, "대여권 확인")
        if result is None:
            return False, "이용권 상태를 확인하지 못했습니다", None
        series_has_waitfree = (result.get("series") or {}).get("is_waitfree")
        if series_has_waitfree is None:
            return False, "작품의 기다무 정보를 확인하지 못했습니다", None
        if series_has_waitfree and not (result.get("single") or {}).get("waitfree_block"):
            return False, "기다무로 열 수 있는 회차라 대여권을 쓰지 않습니다", None
        count = int((result.get("my") or {}).get("ticket_rental_count") or 0)
        ticket_type = (result.get("available") or {}).get("ticket_rental_type")
        if ticket_type != RENTAL_TICKET_TYPE or count < 1:
            return False, "사용할 수 있는 대여권이 없습니다" + (f"(가능한 이용권: {ticket_type})" if ticket_type and ticket_type != RENTAL_TICKET_TYPE else ""), count
        status, used = await self._request_json(
            "POST", TICKET_USE_URL, data={"product_id": product_id, "ticket_type": RENTAL_TICKET_TYPE}, label=f"대여권 사용 product_id={product_id}"
        )
        if used is not None:
            if used.get("result_code") == 0:
                return True, "대여권으로 열었습니다", count - 1
            return False, f"대여권 사용에 실패했습니다(HTTP {status})", count
        # 응답을 받지 못했다 — 서버가 처리했을 수도 있으니, 다시 보내지 않고 대여 중이 됐는지만 확인한다
        check = await self._ready_to_use(product_id, "대여권 사용 확인")
        if check is not None and (check.get("single") or {}).get("read_access_type") == "RAT2":
            return True, "대여권으로 열었습니다(응답은 못 받았지만 대여 중으로 확인됨)", count - 1
        return False, f"대여권 사용 결과를 확인하지 못했습니다(HTTP {status}) — 다시 시도하지 않습니다", count

    async def fetch_series_item(self, series_id: int) -> dict | None:
        """작품 기본 정보(제목/설명/장르/연령/연재 상태/작가). 회차 목록의 첫 페이지 응답에 들어 있어서 회차 1개만 요청해 얻는다
        (전체 회차를 넘기지 않으므로 가볍다). 못 받으면 None."""
        _, data = await self._request_json(
            "GET", PRODUCT_LIST_URL,
            params={"series_id": series_id, "cursor_index": 0, "cursor_direction": "NEXT", "window_size": 1, "sort_type": "asc"},
            label=f"작품 정보 series_id={series_id}",
        )
        result = (data or {}).get("result") or {}
        return result.get("series_item") or None

    async def fetch_about(self, series_id: int) -> dict | None:
        """작품 "정보" 탭: 글/그림/원작 작가(role), 테마 키워드 등. 못 받으면 None."""
        _, data = await self._request_json("GET", ABOUT_URL, params={"series_id": series_id}, label=f"작품 정보 series_id={series_id}")
        if not data or data.get("result_code") != 0:
            return None
        return data.get("result") or None

    async def viewer_image_urls(self, series_id: int, product_id: int) -> list[str] | None:
        status, data = await self._request_json(
            "GET", VIEWER_DATA_URL, params={"series_id": series_id, "product_id": product_id},
            label=f"이미지 목록 product_id={product_id}",
        )
        if not data:
            # 서버가 이 회차를 열어 주지 않은 경우(HTTP 403 등)는 예전에는 아무 로그도 남지 않았다 — 상태를 원인으로 남긴다(요청 예외는 _request_json이 이미 기록)
            self.last_failure = f"이미지 목록 요청 실패(HTTP {status})" if status else "이미지 목록 요청 실패(네트워크 오류)"
            if status:
                log.warning("카카오페이지 이미지 목록을 받지 못했습니다 (series_id=%s, product_id=%s): HTTP %s", series_id, product_id, status)
            return None
        files = (((data.get("viewer_data") or {}).get("imageDownloadData") or {}).get("files")) or []
        urls = [f["secureUrl"] for f in sorted(files, key=lambda f: f.get("no", 0)) if f.get("secureUrl")]
        if not urls:
            self.last_failure = f"이미지 목록이 비어 있음(result_code={data.get('result_code')})"
            log.warning("카카오페이지 이미지 목록이 비어 있습니다 (series_id=%s, product_id=%s): result_code=%s", series_id, product_id, data.get("result_code"))
            return None
        return urls

    async def download_image(self, url: str) -> tuple[bytes, str] | None:
        """이미지 하나를 (바이트, Content-Type)으로. 실패하면 잠깐 쉬고 몇 번 더 시도하고, 그래도 안 되면 None.
        서명된 주소(token/signature)는 한 글자만 달라져도 404가 되어서, 주소를 다시 인코딩하지 않고 받은 그대로 보낸다."""
        target = URL(url, encoded=True)
        for attempt in range(_IMAGE_RETRIES):
            try:
                async with self._session.get(target, headers=self._headers(image=True), timeout=self._timeout) as response:
                    self._absorb_cookies(response)
                    if response.status == 200:
                        body = await response.read()
                        if body:
                            return body, response.headers.get("Content-Type", "")
                    else:
                        # 토큰/서명은 남기지 않고, 어느 서버의 어느 경로가 어떤 응답이었는지만 기록한다(원인 파악용)
                        snippet = (await response.text(errors="replace"))[:120].replace("\n", " ") if response.status != 200 else ""
                        log.warning("카카오페이지 이미지 받기 실패: HTTP %s (%s%s) %s", response.status, target.host, target.path, snippet)
                        self.last_failure = f"이미지 받기 실패(HTTP {response.status})"
            except Exception as e:
                log.warning("카카오페이지 이미지 받기 예외: %s", e)
                self.last_failure = f"이미지 받기 실패({type(e).__name__})"
            if attempt < _IMAGE_RETRIES - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
        return None


def client_from_saved_cookies(session: aiohttp.ClientSession, timeout_seconds: int) -> KakaoPageClient | None:
    """저장된 로그인 쿠키로 클라이언트를 만든다. 저장된 쿠키가 없으면 None."""
    cookies = kakao_page_auth.load_cookies()
    if not cookies:
        return None
    return KakaoPageClient(session, kakao_page_auth.cookie_map(cookies), timeout_seconds)


def new_session() -> aiohttp.ClientSession:
    """카카오페이지 호출용 세션 — 쿠키는 KakaoPageClient가 직접 관리하므로 세션엔 쿠키 저장소를 두지 않는다."""
    return aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())


def client_or_anonymous(session: aiohttp.ClientSession, timeout_seconds: int) -> KakaoPageClient:
    """저장된 로그인 쿠키가 있으면 그걸로, 없으면 쿠키 없이 부르는 클라이언트(작품 정보처럼 로그인이 필요 없는 조회용)."""
    return client_from_saved_cookies(session, timeout_seconds) or KakaoPageClient(session, {}, timeout_seconds)


def persist_refreshed_cookies(client: KakaoPageClient) -> None:
    """서버가 로그인 유지를 위해 새로 내려준 쿠키가 있으면 저장해서, 다음 실행에도 이어지게 한다."""
    if client.cookies_changed:
        kakao_page_auth.update_cookie_values(client.cookies)
        client.cookies_changed = False
