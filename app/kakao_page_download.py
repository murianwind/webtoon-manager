"""
카카오페이지 다운로드 엔진. 로그인 쿠키(kakao_page_auth)로 카카오페이지 API를 호출해서 회차 목록을 받고,
폴더에 이미 있는 파일과 비교해 받을 회차를 정하고, 회차를 zip으로 저장한다. 실제 브라우저 캡처(HAR)와
사용자의 실제 폴더 목록(예전 카카오 도구가 받은 1,935개 파일)으로 확인한 것들:

- 회차 목록: content/product/list — 첫 페이지는 cursor_direction=INIT, 이어서는 마지막 항목의
  cursor_index로 NEXT(sort_type=asc)를 부르고, has_next가 false가 될 때까지 넘긴다.
- 이미지 목록: viewer/data → viewer_data.imageDownloadData.files[].secureUrl(서명된 임시 주소).
- 로그인 확인: user/get_profile → result_code 0 이고 profile.uid가 있으면 로그인 상태.
- **파일 앞의 번호는 "N화"가 아니라 회차 순서 번호다** — 프롤로그/예고편/후기도 번호를 하나씩 차지해서(예:
  `0003_프롤로그`, `0002_1화`) 번호와 화수의 차이가 작품마다 다르다. 번호는 사이트의 order_value를 따르고, 프롤로그
  같은 이미지 회차도 받는다(숨김 처리된 것만 뺀다). **동영상(예: "동영상 트레일러")은 받을 수 없어서 목록에서 빼고,
  그만큼 뒤 회차의 번호를 당긴다** — 예전 도구는 동영상에 번호를 주지 않았기 때문에, 그러지 않으면 같은 회차의
  번호가 하나씩 어긋난다.
- **기다무(RT05) 대여권**: ticket/my로 기다무를 쓸 수 있는지 보고(waitfree.charged_complete), ticket/ready_to_use가
  가리키는 대여권 종류가 RT05일 때만 ticket/use(product_id, ticket_type=RT05)로 열고, 이어서 viewer/data로 이미지를
  받는다. **결제(캐시/소장권 구매)는 이 코드에 아예 없고, RT05 이외의 대여권(선물권 RT06 등)도 자동으로 쓰지 않는다.**
  선물권은 사용자가 웹에서 쓰면 그 회차가 "대여 중"이 되어 그대로 받을 수 있다.
- 파일 이름은 `{번호 4자리}_{부제목}.zip`(예: 0384_384화 복국 (2).zip) — 페이지 수는 붙이지 않는다. 예전 도구가
  받은 파일은 뒤에 `#페이지수`가 붙어 있는데(예: 0384_384화 복국 (2)#48.zip), 그 파일도 같은 번호로 인식한다.
  저장 위치는 다운로드 루트 아래 `{작품 제목}` 폴더(금지문자는 카카오 도구와 같은 규칙).

받을 회차는 그 작품 폴더의 상태로 정한다(자동/수동 공통):
  1) 폴더가 없거나 zip이 하나도 없으면 폴더를 만들고 처음부터 전부 받는다.
  2) zip이 여러 개면 회차를 비교해서 누락된 회차를 번호 순서대로 받는다.
  3) zip이 딱 하나면 그 파일을 "여기까지 받았다"는 표식으로 보고 그 이후 회차부터 받는다.
     (cover.jpg, info.xml은 세지 않는다.)
  규칙 2에서도 폴더에 있는 가장 이른 파일보다 앞 회차는 누락으로 보지 않는다 — 표식(규칙 3)으로 시작한
  폴더는 이후 회차를 받으면 파일이 여러 개가 되는데, 그때 표식보다 앞 회차를 전부 "누락"으로 보고 다시 받으면
  안 되기 때문이다(예: 개미 43화 표식으로 시작했는데 1~42화를 받기 시작하면 안 된다). 앞 회차가 필요하면
  수동 다운로드에서 골라 받는다.

읽을 수 있는 회차(무료, 또는 이미 대여 중)를 받는다. 잠긴 회차를 만나면, 기다무를 쓸 수 있을 때 그 회차 하나를
기다무로 열어서 받고(기다무는 작품당 한 장씩 주기적으로 충전된다), 그 뒤 잠긴 회차부터는 멈춰서 "대기"로 알린다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from app import comicinfo, kakao_api, kakao_cover, kakao_page_auth, repository
from app.file_utils import remove_forbidden_str_kakao

log = logging.getLogger(__name__)

_API = "https://bff-page.kakao.com/api/gateway/api"
PROFILE_URL = f"{_API}/v1/user/get_profile"
PRODUCT_LIST_URL = kakao_api.KAKAO_PRODUCT_LIST_URL
VIEWER_DATA_URL = f"{_API}/v1/viewer/data"
TICKET_MY_URL = f"{_API}/v1/ticket/my"
TICKET_READY_URL = f"{_API}/v1/ticket/ready_to_use"
TICKET_USE_URL = f"{_API}/v1/ticket/use"
ABOUT_URL = f"{_API}/v1/content/about"
WAITFREE_TICKET_TYPE = "RT05"  # 기다무 대여권 — 이 프로그램이 자동으로 쓰는 유일한 이용권 종류
_IMAGE_SLIDE_TYPE = "SD03"  # 이미지 회차(그 밖의 종류는 동영상 등이라 받지 않는다)

_LIST_WINDOW_SIZE = 25
_MAX_LIST_PAGES = 80  # 25 x 80 = 2000회차 — 이상 응답으로 끝없이 넘기는 걸 막는 안전 상한
_PAGE_INTERVAL_SECONDS = 0.3
IMAGE_CONCURRENCY = 4
_IMAGE_RETRIES = 3
_ACCESSIBLE_PURCHASE_TYPES = {"rent", "own", "purchased"}
_KST = timezone(timedelta(hours=9))

_EXISTING_ZIP_RE = re.compile(r"^(\d+)_(.+?)(?:#\d+)?\.zip$", re.IGNORECASE)
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/avif": ".avif",
}

DOWNLOAD_ROOT_SETTING_KEY = "kakao_download_root"

# 자동 다운로드와 수동 다운로드가 같은 작품(같은 zip)을 동시에 받지 않게 하는 락
download_lock = asyncio.Lock()


# ── 회차 / 폴더 규칙 ─────────────────────────────────────────────────────

@dataclass
class Episode:
    product_id: int
    title: str
    number: int  # 사이트의 회차 순서 번호(order_value) — 파일 앞 번호와 같은 체계
    subtitle: str
    is_free: bool
    accessible: bool  # 지금 바로 읽을 수 있음(무료 또는 대여 기간 안)
    page_count: int
    hidden: bool
    rent_expire: str | None = None  # 대여 중이면 대여 만료 시각
    waitfree_blocked: bool = False  # 기다무로 열 수 없는 회차(최신 회차 등)


@dataclass
class TicketInfo:
    """작품의 이용권 현황 — 카카오페이지 작품 화면의 "대여권 N장 | 소장권 N장 | 기다무" 표시와 같은 값."""

    rental_count: int = 0  # 대여권(선물권 등)
    own_count: int = 0  # 소장권
    waitfree_ready: bool = False  # 지금 기다무를 쓸 수 있음
    waitfree_available_at: str | None = None  # 기다무를 다시 쓸 수 있게 되는 시각(쓸 수 없을 때)


@dataclass
class ExistingFile:
    number: int
    subtitle: str  # normalize_subtitle을 거친 부제목
    name: str


@dataclass
class MarkerInfo:
    number: int  # 폴더에 있는 표식 파일의 번호
    subtitle: str
    resolved_number: int  # 사이트 기준으로 확인한 표식의 실제 위치(번호가 밀려 있으면 부제목으로 보정)
    warning: bool  # 번호로도 부제목으로도 사이트의 회차와 맞춰보지 못함


@dataclass
class EpisodeRow:
    episode: Episode
    downloaded: bool  # 폴더에 이미 받은 파일이 있음(번호 또는 부제목이 같음)
    before_start: bool = False  # 폴더의 가장 이른 파일보다 앞 회차 — 자동으로는 받지 않는다(수동으로는 받을 수 있다)


@dataclass
class DownloadPlan:
    mode: str  # new_folder | single_marker | compare
    rows: list[EpisodeRow] = field(default_factory=list)
    to_download: list[Episode] = field(default_factory=list)  # 지금 받을 수 있는 것(순서대로, 첫 잠긴 회차 앞까지)
    locked: list[Episode] = field(default_factory=list)  # 대여권이 필요한 것(첫 잠긴 회차부터 뒤 전부)
    marker: MarkerInfo | None = None
    existing_count: int = 0


@dataclass
class RunResult:
    downloaded: list[int]
    downloaded_items: list[tuple[int, str]]  # (번호, 부제목)
    failed: int | None
    plan: DownloadPlan
    error: str | None = None
    title: str = ""  # 실제로 쓴 폴더 제목(카카오페이지에 등록된 작품 제목)
    ticket_used: int | None = None  # 기다무로 열어서 받은 회차 번호


@dataclass
class SelectedResult:
    downloaded: list[int] = field(default_factory=list)
    downloaded_items: list[tuple[int, str]] = field(default_factory=list)
    replaced: list[int] = field(default_factory=list)  # 이미 받은 회차를 다시 받아 교체한 것(downloaded의 일부)
    failed: list[int] = field(default_factory=list)
    skipped_locked: list[int] = field(default_factory=list)
    ticket_used: list[int] = field(default_factory=list)  # 기다무로 열어서 받은 회차
    not_found: list[int] = field(default_factory=list)
    title: str = ""


def _file_safe(subtitle: str) -> str:
    """파일 이름에 넣을 부제목. 폴더 이름과 같은 카카오 도구의 금지문자 치환을 쓰되, 마침표(.)는 그대로 둔다 —
    실제 예전 파일 1,935개를 전부 대조해보니 폴더 이름 규칙과 다른 건 이것 하나뿐이었다(예: `0002_1화. 미명귀 一`).
    다만 이름 끝의 마침표는 윈도우에서 문제가 되므로 뗀다."""
    return remove_forbidden_str_kakao(subtitle or "").replace("․", ".").rstrip(". ")


def normalize_subtitle(subtitle: str) -> str:
    """부제목을 비교용으로 맞춘다 — 파일 이름에 들어갈 때와 같은 치환을 거치고 공백을 정리한다."""
    return " ".join(_file_safe(subtitle).split())


def derive_subtitle(title: str, series_title: str) -> str:
    """카카오페이지 회차 제목은 "작품제목 26화"처럼 작품 제목이 앞에 붙어 있어서, 그걸 뺀 "26화"를 부제목으로 쓴다."""
    text = (title or "").strip()
    if series_title and text.startswith(series_title):
        rest = text[len(series_title):].strip()
        if rest:
            return rest
    return text


def episode_zip_name(number: int, subtitle: str) -> str:
    return f"{number:04d}_{_file_safe(subtitle)}.zip"


def episode_state(episode: Episode) -> str:
    """분석 화면의 상태 — free(무료) / owned(보유: 대여 중) / locked(잠금)."""
    if episode.is_free:
        return "free"
    return "owned" if episode.accessible else "locked"


def parse_existing_zip_name(name: str) -> ExistingFile | None:
    match = _EXISTING_ZIP_RE.match(name)
    if not match:
        return None
    return ExistingFile(number=int(match.group(1)), subtitle=normalize_subtitle(match.group(2)), name=name)


def scan_existing_files(folder: Path) -> list[ExistingFile]:
    """폴더 안의 회차 zip들(번호순). cover.jpg/info.xml/임시 파일/이름 규칙이 다른 파일은 세지 않는다."""
    if not folder.is_dir():
        return []
    files = [f for f in (parse_existing_zip_name(entry.name) for entry in folder.iterdir()) if f is not None]
    return sorted(files, key=lambda f: f.number)


def plan_by_folder_rules(episodes: list[Episode], existing: list[ExistingFile]) -> DownloadPlan:
    """폴더 규칙(모듈 설명 참고)으로 받을 회차를 정한다. 받을 수 있는 회차를 앞에서부터 이어서 받다가 처음
    잠긴 회차에서 멈춘다 — 순서를 건너뛰지 않아야 "번호 순서대로"가 지켜진다."""
    visible = sorted((e for e in episodes if not e.hidden and e.number > 0), key=lambda e: e.number)
    marker: MarkerInfo | None = None

    lower_bound = 0  # 이 번호보다 앞 회차는 자동으로 받지 않는다
    if not existing:
        mode = "new_folder"
    elif len(existing) == 1:
        mode = "single_marker"
        only = existing[0]
        resolved, warning = only.number, False
        at_number = next((e for e in visible if e.number == only.number), None)
        if at_number is None or normalize_subtitle(at_number.subtitle) != only.subtitle:
            # 번호가 사이트와 안 맞으면 부제목으로 위치를 다시 찾는다(예전 도구와 번호가 밀려 있는 경우)
            same_title = [e for e in visible if normalize_subtitle(e.subtitle) == only.subtitle]
            if len(same_title) == 1:
                resolved = same_title[0].number
            else:
                warning = True
        marker = MarkerInfo(number=only.number, subtitle=only.subtitle, resolved_number=resolved, warning=warning)
        lower_bound = resolved
    else:
        mode = "compare"
        lower_bound = min(f.number for f in existing)

    have_numbers = {f.number for f in existing}
    have_subtitles = {f.subtitle for f in existing}
    plan = DownloadPlan(mode=mode, marker=marker, existing_count=len(existing))
    blocked = False
    for episode in visible:
        done = episode.number in have_numbers or normalize_subtitle(episode.subtitle) in have_subtitles
        skipped = not done and episode.number < lower_bound
        plan.rows.append(EpisodeRow(episode=episode, downloaded=done, before_start=skipped))
        if done or skipped:
            continue
        if not blocked and episode.accessible:
            plan.to_download.append(episode)
        else:
            blocked = True
            plan.locked.append(episode)
    return plan


def effective_download_root(default_root: str) -> str:
    """카카오페이지 전용 다운로드 폴더(설정)가 있으면 그것을, 없으면 기본 다운로드 폴더를 쓴다."""
    return repository.get_setting(DOWNLOAD_ROOT_SETTING_KEY) or default_root


def series_folder(download_root: str, title: str) -> Path:
    return Path(download_root) / remove_forbidden_str_kakao(title)


def _is_accessible(item: dict, now_kst: datetime) -> bool:
    if item.get("is_free"):
        return True
    purchase = ((item.get("service_property") or {}).get("purchase_info")) or {}
    if purchase.get("purchase_type") not in _ACCESSIBLE_PURCHASE_TYPES:
        return False
    expire = purchase.get("rent_expire_dt")
    if not expire:
        return True
    try:
        return datetime.strptime(expire, "%Y-%m-%d %H:%M:%S") > now_kst
    except ValueError:
        return True


def is_video_item(item: dict) -> bool:
    """이미지가 아닌 회차(동영상 트레일러 등). slide_type이 이미지(SD03)가 아니거나 제목에 "동영상"이 있으면 동영상으로 본다."""
    slide_type = item.get("slide_type")
    if slide_type and slide_type != _IMAGE_SLIDE_TYPE:
        return True
    return "동영상" in (item.get("title") or "")


def _parse_episode(item: dict, series_title: str, now_kst: datetime) -> Episode:
    title = item.get("title") or ""
    purchase = ((item.get("service_property") or {}).get("purchase_info")) or {}
    accessible = _is_accessible(item, now_kst)
    return Episode(
        product_id=item["product_id"], title=title, number=int(item.get("order_value") or 0),
        subtitle=derive_subtitle(title, series_title), is_free=bool(item.get("is_free")), accessible=accessible,
        page_count=int(item.get("page_count") or 0), hidden=bool(item.get("hidden")),
        rent_expire=purchase.get("rent_expire_dt") if accessible and not item.get("is_free") else None,
        waitfree_blocked=bool(item.get("waitfree_blocked")),
    )


def _image_extension(content_type: str, body: bytes) -> str:
    known = _IMAGE_EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())
    if known:
        return known
    if body.startswith(b"\xff\xd8"):
        return ".jpg"
    if body.startswith(b"\x89PNG"):
        return ".png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    if body[:3] == b"GIF":
        return ".gif"
    return ".jpg"


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
        video_orders: list[int] = []  # 동영상 회차의 순서 번호(뒤 회차의 번호를 그만큼 당기는 데 쓴다)
        cursor, direction = 0, "INIT"
        for _ in range(_MAX_LIST_PAGES):
            params = {"series_id": series_id, "cursor_index": cursor, "cursor_direction": direction, "window_size": _LIST_WINDOW_SIZE}
            if direction == "NEXT":
                params["sort_type"] = "asc"
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
                if is_video_item(item):
                    video_orders.append(int(item.get("order_value") or 0))
                    episodes[product_id] = None  # 다시 세지 않게 표시만 해 둔다
                else:
                    episodes[product_id] = _parse_episode(item, series_item.get("title", ""), now_kst)
                new_count += 1
            cursor = entries[-1].get("cursor_index") if entries else None
            if not result.get("has_next") or new_count == 0 or cursor is None:
                break
            direction = "NEXT"
            await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
        images = [e for e in episodes.values() if e is not None and e.number > 0]
        for episode in images:
            episode.number -= sum(1 for order in video_orders if order < episode.number)
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
        )

    async def use_waitfree_ticket(self, product_id: int) -> tuple[bool, str]:
        """회차 하나를 기다무 대여권으로 연다. (성공 여부, 사람이 읽을 이유). 이 회차에 실제로 쓸 수 있는 이용권이
        기다무(RT05)라고 서버가 알려줄 때만 쓴다 — 선물권 등 다른 이용권이나 결제가 필요한 경우는 쓰지 않는다."""
        _, ready = await self._request_json(
            "GET", TICKET_READY_URL, params={"product_id": product_id, "include_series": "true"}, label=f"이용권 확인 product_id={product_id}"
        )
        if not ready or ready.get("result_code") != 0:
            return False, "이용권 상태를 확인하지 못했습니다"
        result = ready.get("result") or {}
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

    async def fetch_about(self, series_id: int) -> dict | None:
        """작품 "정보" 탭: 글/그림/원작 작가(role), 테마 키워드 등. 못 받으면 None."""
        _, data = await self._request_json("GET", ABOUT_URL, params={"series_id": series_id}, label=f"작품 정보 series_id={series_id}")
        if not data or data.get("result_code") != 0:
            return None
        return data.get("result") or None

    async def viewer_image_urls(self, series_id: int, product_id: int) -> list[str] | None:
        _, data = await self._request_json(
            "GET", VIEWER_DATA_URL, params={"series_id": series_id, "product_id": product_id},
            label=f"이미지 목록 product_id={product_id}",
        )
        if not data:
            return None
        files = (((data.get("viewer_data") or {}).get("imageDownloadData") or {}).get("files")) or []
        urls = [f["secureUrl"] for f in sorted(files, key=lambda f: f.get("no", 0)) if f.get("secureUrl")]
        return urls or None

    async def download_image(self, url: str) -> tuple[bytes, str] | None:
        """이미지 하나를 (바이트, Content-Type)으로. 실패하면 잠깐 쉬고 몇 번 더 시도하고, 그래도 안 되면 None."""
        for attempt in range(_IMAGE_RETRIES):
            try:
                async with self._session.get(url, headers=self._headers(image=True), timeout=self._timeout) as response:
                    self._absorb_cookies(response)
                    if response.status == 200:
                        body = await response.read()
                        if body:
                            return body, response.headers.get("Content-Type", "")
                    else:
                        log.warning("카카오페이지 이미지 받기 실패: HTTP %s", response.status)
            except Exception as e:
                log.warning("카카오페이지 이미지 받기 예외: %s", e)
            if attempt < _IMAGE_RETRIES - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
        return None


def client_from_saved_cookies(session: aiohttp.ClientSession, timeout_seconds: int) -> KakaoPageClient | None:
    """저장된 로그인 쿠키로 클라이언트를 만든다. 저장된 쿠키가 없으면 None."""
    cookies = kakao_page_auth.load_cookies()
    if not cookies:
        return None
    return KakaoPageClient(session, kakao_page_auth.cookie_map(cookies), timeout_seconds)


def persist_refreshed_cookies(client: KakaoPageClient) -> None:
    """서버가 로그인 유지를 위해 새로 내려준 쿠키가 있으면 저장해서, 다음 실행에도 이어지게 한다."""
    if client.cookies_changed:
        kakao_page_auth.update_cookie_values(client.cookies)
        client.cookies_changed = False


# ── 받기 ─────────────────────────────────────────────────────────────────

async def download_episode(client: KakaoPageClient, series_id: int, episode: Episode, folder: Path) -> Path | None:
    """회차 하나를 zip으로 저장하고 경로를 돌려준다. 이미지 목록을 못 받거나 이미지가 하나라도 안 받히면
    아무것도 남기지 않고 None — 일부만 담긴 zip이 있으면 다음에 "이미 받은 회차"로 오인하기 때문이다.
    zip은 임시 이름으로 완성한 뒤 한 번에 이름을 바꿔서, 도중에 끊겨도 반쪽짜리가 남지 않는다.
    같은 번호의 예전 파일(이름이 다른 것, 예: 뒤에 #페이지수가 붙은 것)이 있으면 새 파일을 다 저장한 뒤에
    지운다 — 다시 받기를 하면 한 회차에 파일이 둘이 되지 않도록."""
    urls = await client.viewer_image_urls(series_id, episode.product_id)
    if not urls:
        return None

    semaphore = asyncio.Semaphore(IMAGE_CONCURRENCY)

    async def _one(url: str):
        async with semaphore:
            return await client.download_image(url)

    images = await asyncio.gather(*[_one(u) for u in urls])
    if any(image is None for image in images):
        return None

    folder.mkdir(parents=True, exist_ok=True)
    final_path = folder / episode_zip_name(episode.number, episode.subtitle)
    part_path = folder / (final_path.name + ".part")
    zero_fill = max(3, len(str(len(images))))
    try:
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, (body, content_type) in enumerate(images, 1):
                archive.writestr(f"{str(index).zfill(zero_fill)}{_image_extension(content_type, body)}", body)
        os.replace(part_path, final_path)
    except Exception as e:
        log.error("카카오페이지 회차 저장 실패 (%s): %s", final_path, e)
        part_path.unlink(missing_ok=True)
        return None
    _remove_superseded(folder, episode.number, keep=final_path)
    return final_path


def _remove_superseded(folder: Path, number: int, *, keep: Path) -> None:
    """같은 번호의 다른 이름 zip(다시 받기로 교체된 예전 파일)을 지운다."""
    for entry in scan_existing_files(folder):
        if entry.number == number and entry.name != keep.name:
            try:
                (folder / entry.name).unlink()
            except OSError as e:
                log.warning("교체된 예전 파일을 지우지 못했습니다 (%s): %s", entry.name, e)


async def run_download(
    client: KakaoPageClient, *, series_id: int, title: str | None, download_root: str, max_episodes: int, on_progress=None,
) -> RunResult:
    """폴더 규칙(자동 다운로드용)으로 받을 회차를 정해 순서대로 최대 max_episodes개 받는다. 실패하면 그 회차에서 멈춘다.
    폴더 이름은 title(있으면), 없으면 카카오페이지에 등록된 작품 제목을 쓴다."""
    listing = await client.list_episodes(series_id)
    if listing is None:
        return RunResult([], [], None, DownloadPlan(mode="new_folder"), "회차 목록을 가져오지 못했습니다.")
    series_item, episodes = listing
    folder_title = title or series_item.get("title") or str(series_id)
    folder = series_folder(download_root, folder_title)
    plan = plan_by_folder_rules(episodes, scan_existing_files(folder))
    downloaded: list[int] = []
    items: list[tuple[int, str]] = []
    failed: int | None = None
    for episode in plan.to_download[:max_episodes]:
        path = await download_episode(client, series_id, episode, folder)
        if path is None:
            failed = episode.number
            if on_progress:
                on_progress(f"{episode.number}번 회차 받기 실패 — 여기서 멈춥니다(다음에 이 회차부터 다시 시도)")
            break
        downloaded.append(episode.number)
        items.append((episode.number, episode.subtitle))
        if on_progress:
            on_progress(f"{episode.number}번 회차 받음 ({path.name})")
    ticket_used: int | None = None
    if failed is None and len(downloaded) < max_episodes and plan.locked:
        # 받을 수 있는 건 다 받았고 잠긴 회차가 남았으면, 첫 잠긴 회차를 기다무로 열어 받는다(한 장뿐이라 그 뒤는 대기)
        ticket_used, failed = await _download_first_locked_with_waitfree(
            client, series_id, plan.locked[0], folder, downloaded, items, on_progress
        )
    if downloaded:
        await write_series_metadata(series_item, series_id, folder, on_progress, await client.fetch_about(series_id))
    return RunResult(downloaded, items, failed, plan, title=folder_title, ticket_used=ticket_used)


async def _download_first_locked_with_waitfree(client, series_id, episode, folder, downloaded, items, on_progress) -> tuple[int | None, int | None]:
    """(기다무로 연 회차 번호 또는 None, 받기에 실패한 회차 번호 또는 None). 기다무를 못 쓰면 아무것도 안 한다."""
    if episode.waitfree_blocked:
        return None, None
    info = await client.ticket_info(series_id)
    if info is None or not info.waitfree_ready:
        return None, None
    ok, reason = await client.use_waitfree_ticket(episode.product_id)
    if not ok:
        if on_progress:
            on_progress(f"{episode.number}번 회차: {reason} — 여기서 멈춥니다")
        return None, None
    if on_progress:
        on_progress(f"{episode.number}번 회차를 기다무로 열었습니다")
    path = await download_episode(client, series_id, episode, folder)
    if path is None:
        if on_progress:
            on_progress(f"{episode.number}번 회차 받기 실패 — 기다무로 이미 열렸으니 대여 기간 안에 다음 실행에서 다시 받습니다")
        return episode.number, episode.number
    downloaded.append(episode.number)
    items.append((episode.number, episode.subtitle))
    if on_progress:
        on_progress(f"{episode.number}번 회차 받음 ({path.name})")
    return episode.number, None


async def download_selected(
    client: KakaoPageClient, *, series_id: int, title: str | None, numbers: list[int], download_root: str, on_progress=None,
) -> SelectedResult:
    """수동 다운로드 — 분석 표에서 고른 회차들을 번호순으로 받는다. 이미 받은 회차도 고르면 다시 받아 교체한다
    (자동 다운로드와 달리 수동은 "다시 받기"가 목적일 수 있다). 잠긴 회차는 건너뛰고, 하나가 실패해도 나머지는
    계속한다(직접 고른 것이라 순서를 끊을 이유가 없다). 다시 받다가 실패하면 원래 파일은 그대로 남는다."""
    result = SelectedResult()
    listing = await client.list_episodes(series_id)
    if listing is None:
        result.not_found = sorted(set(numbers))
        return result
    series_item, episodes = listing
    result.title = title or series_item.get("title") or str(series_id)
    folder = series_folder(download_root, result.title)
    plan = plan_by_folder_rules(episodes, scan_existing_files(folder))
    rows = {row.episode.number: row for row in plan.rows}
    ticket = None  # 잠긴 회차를 고른 경우에만 조회한다
    waitfree_spent = False  # 기다무는 한 장이라 고른 잠긴 회차 중 번호가 가장 앞선 하나만 연다
    for number in sorted(set(numbers)):
        row = rows.get(number)
        if row is None:
            result.not_found.append(number)
            continue
        if not row.episode.accessible:
            opened = False
            reason = "잠김(대여권 필요)"
            if waitfree_spent:
                reason = "기다무는 한 장이라 하나만 열 수 있습니다"
            elif row.episode.waitfree_blocked:
                reason = "기다무로 열 수 없는 회차입니다"
            else:
                ticket = ticket or await client.ticket_info(series_id) or TicketInfo()
                if ticket.waitfree_ready:
                    opened, reason = await client.use_waitfree_ticket(row.episode.product_id)
                    waitfree_spent = True
            if not opened:
                result.skipped_locked.append(number)
                if on_progress:
                    on_progress(f"{number}번 회차: {reason} — 건너뜀")
                continue
            result.ticket_used.append(number)
            if on_progress:
                on_progress(f"{number}번 회차를 기다무로 열었습니다")
        path = await download_episode(client, series_id, row.episode, folder)
        if path is None:
            result.failed.append(number)
            if on_progress:
                on_progress(f"❌ {number}번 회차 받기 실패" + (" (기존 파일은 그대로 둡니다)" if row.downloaded else ""))
        else:
            result.downloaded.append(number)
            result.downloaded_items.append((number, row.episode.subtitle))
            if row.downloaded:
                result.replaced.append(number)
            if on_progress:
                on_progress(f"✅ {number}번 회차 {'다시 받아 교체' if row.downloaded else '완료'} ({path.name})")
    if result.downloaded:
        await write_series_metadata(series_item, series_id, folder, on_progress, await client.fetch_about(series_id))
    return result


def split_authors(about: dict | None) -> tuple[list[str], list[str], list[str]]:
    """작품 "정보"의 author_list에서 (글, 그림, 원작) 이름 목록. 같은 이름은 한 번만."""
    groups: dict[str, list[str]] = {"writer": [], "illustrator": [], "original_author": []}
    for author in (about or {}).get("author_list") or []:
        names = groups.get(author.get("role"))
        if names is not None and author.get("name") and author["name"] not in names:
            names.append(author["name"])
    return groups["writer"], groups["illustrator"], groups["original_author"]


def authors_to_register(about: dict | None) -> list[str]:
    """구독할 때 관심 작가로 등록할 이름 — 원작자가 있으면 원작자, 없으면 글 작가(네이버와 같은 규칙)."""
    writers, _, originals = split_authors(about)
    return originals or writers


async def write_series_metadata(series_item: dict, series_id: int, folder: Path, on_progress=None, about: dict | None = None) -> None:
    """받은 뒤 작품 폴더에 info.xml을 쓰고(네이버와 같이 받을 때마다 최신 정보로 덮어씀), 표지(cover)가 없으면
    카카오페이지 공식 표지를 받아 저장한다. 실패해도 다운로드 결과에는 영향을 주지 않는다."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "info.xml").write_text(comicinfo.build_kakao_comicinfo_xml(series_item, series_id, about), encoding="utf-8")
        if not any(folder.glob("cover.*")):
            cover = await asyncio.to_thread(kakao_cover.fetch_official_cover_bytes, str(series_id))
            if cover:
                (folder / "cover.jpg").write_bytes(cover)
            elif on_progress:
                on_progress("표지 이미지를 받지 못했습니다(다음에 다시 시도)")
    except Exception as e:
        log.warning("카카오페이지 info.xml/표지 저장 실패 (%s): %s", folder, e)
