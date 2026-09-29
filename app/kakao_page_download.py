"""
카카오페이지 다운로드 엔진. 로그인 쿠키(kakao_page_auth)로 카카오페이지 API를 호출해서 회차 목록을 받고,
받을 회차를 계획하고(이미 받은 것/시작 회차 반영), 회차를 zip으로 저장한다. 실제 브라우저 캡처(HAR)로
확인한 흐름을 따른다:

- 회차 목록: content/product/list — 첫 페이지는 cursor_direction=INIT, 이어서는 마지막 항목의
  cursor_index로 NEXT(sort_type=asc)를 부르고, has_next가 false가 될 때까지 넘긴다.
- 이미지 목록: viewer/data → viewer_data.imageDownloadData.files[].secureUrl(서명된 임시 주소).
- 로그인 확인: user/get_profile → result_code 0 이고 profile.uid가 있으면 로그인 상태.

이 단계(1단계)는 이미 읽을 수 있는 회차(무료, 또는 이미 대여 중)만 받는다. 잠긴 회차는 받지 않고 "대여권
필요"로 알린다 — 대여권을 쓰는 건 다음 단계에서 붙인다.

파일 이름은 `{번호 4자리}_{부제목}.zip`(예: 0027_27화.zip), 저장 위치는 다운로드 루트 아래
`{작품 제목}` 폴더(금지문자는 카카오 도구와 같은 규칙으로 치환)다.
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

from app import kakao_api, kakao_page_auth
from app.file_utils import remove_forbidden_str_kakao

log = logging.getLogger(__name__)

_API = "https://bff-page.kakao.com/api/gateway/api"
PROFILE_URL = f"{_API}/v1/user/get_profile"
PRODUCT_LIST_URL = kakao_api.KAKAO_PRODUCT_LIST_URL
VIEWER_DATA_URL = f"{_API}/v1/viewer/data"

_LIST_WINDOW_SIZE = 25
_MAX_LIST_PAGES = 80  # 25 x 80 = 2000회차 — 이상 응답으로 끝없이 넘기는 걸 막는 안전 상한
_PAGE_INTERVAL_SECONDS = 0.3
IMAGE_CONCURRENCY = 4
_IMAGE_RETRIES = 3
_ACCESSIBLE_PURCHASE_TYPES = {"rent", "own", "purchased"}
_KST = timezone(timedelta(hours=9))

_NUMBER_RE = re.compile(r"(\d+)\s*화")
_LEADING_DIGITS_RE = re.compile(r"^(\d+)")
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/avif": ".avif",
}


# ── 회차 / 계획 ──────────────────────────────────────────────────────────

@dataclass
class Episode:
    product_id: int
    title: str
    number: int | None  # 제목의 "N화"에서 뽑은 번호. 프롤로그/외전처럼 "N화"가 없으면 None
    subtitle: str
    is_free: bool
    accessible: bool  # 지금 바로 읽을 수 있음(무료 또는 대여 기간 안)
    order_value: int
    page_count: int
    hidden: bool


@dataclass
class DownloadPlan:
    to_download: list[Episode] = field(default_factory=list)  # 지금 받을 수 있는 것(순서대로, 첫 잠긴 회차 앞까지)
    locked: list[Episode] = field(default_factory=list)  # 대여권이 필요한 것(첫 잠긴 회차부터 뒤 전부)
    skipped_existing: int = 0
    unnumbered: int = 0


@dataclass
class RunResult:
    downloaded: list[int]
    failed: int | None
    plan: DownloadPlan
    error: str | None = None


def parse_episode_number(title: str) -> int | None:
    found = _NUMBER_RE.findall(title or "")
    return int(found[-1]) if found else None


def derive_subtitle(title: str, series_title: str) -> str:
    """카카오페이지 회차 제목은 "작품제목 26화"처럼 작품 제목이 앞에 붙어 있어서, 그걸 뺀 "26화"를 부제목으로 쓴다."""
    text = (title or "").strip()
    if series_title and text.startswith(series_title):
        rest = text[len(series_title):].strip()
        if rest:
            return rest
    return text


def episode_zip_name(number: int, subtitle: str) -> str:
    return f"{number:04d}_{remove_forbidden_str_kakao(subtitle)}.zip"


def scan_existing_numbers(folder: Path) -> set[int]:
    """폴더에 이미 있는 zip의 회차 번호들 — 새 형식(0027_27화.zip)과 예전 카카오 도구 형식(27_27화#35.zip)이
    둘 다 앞의 숫자로 인식된다. 이걸로 이미 받은 회차를 건너뛴다(대여권을 또 쓰지 않도록)."""
    if not folder.is_dir():
        return set()
    numbers = set()
    for entry in folder.iterdir():
        if entry.suffix.lower() == ".zip":
            match = _LEADING_DIGITS_RE.match(entry.name)
            if match:
                numbers.add(int(match.group(1)))
    return numbers


def plan_downloads(
    episodes: list[Episode], *, start_no: int, last_downloaded_no: int, existing_numbers: set[int]
) -> DownloadPlan:
    """받을 회차를 정한다. 시작 회차 이상이고 마지막으로 받은 번호보다 크며 폴더에 아직 없는 번호 있는 회차를
    번호순으로 보고, 지금 읽을 수 있는 회차를 앞에서부터 이어서 받다가 처음 잠긴 회차에서 멈춘다(순서를
    건너뛰지 않아야 다음 실행이 "이 회차부터"로 이어진다). 숨김 회차와 번호 없는 회차는 받지 않는다."""
    plan = DownloadPlan(unnumbered=sum(1 for e in episodes if e.number is None and not e.hidden))
    numbered = sorted((e for e in episodes if e.number is not None and not e.hidden), key=lambda e: (e.number, e.order_value))
    blocked = False
    for episode in numbered:
        if episode.number < start_no or episode.number <= last_downloaded_no:
            continue
        if episode.number in existing_numbers:
            plan.skipped_existing += 1
            continue
        if not blocked and episode.accessible:
            plan.to_download.append(episode)
        else:
            blocked = True
            plan.locked.append(episode)
    return plan


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


def _parse_episode(item: dict, series_title: str, now_kst: datetime) -> Episode:
    title = item.get("title") or ""
    return Episode(
        product_id=item["product_id"], title=title, number=parse_episode_number(title),
        subtitle=derive_subtitle(title, series_title), is_free=bool(item.get("is_free")),
        accessible=_is_accessible(item, now_kst), order_value=int(item.get("order_value") or 0),
        page_count=int(item.get("page_count") or 0), hidden=bool(item.get("hidden")),
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

    async def _request_json(self, method: str, url: str, *, params: dict | None = None, label: str):
        """(HTTP 상태, JSON 또는 None). 요청 자체가 실패하면 (None, None)."""
        try:
            if method == "POST":
                request = self._session.post(url, data={}, headers=self._headers(post=True), timeout=self._timeout)
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
        cursor, direction = 0, "INIT"
        for _ in range(_MAX_LIST_PAGES):
            params = {"series_id": series_id, "cursor_index": cursor, "cursor_direction": direction, "window_size": _LIST_WINDOW_SIZE}
            if direction == "NEXT":
                params["sort_type"] = "asc"
            status, data = await self._request_json("GET", PRODUCT_LIST_URL, params=params, label=f"회차 목록 series_id={series_id}")
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
                episodes[product_id] = _parse_episode(item, series_item.get("title", ""), now_kst)
                new_count += 1
            cursor = entries[-1].get("cursor_index") if entries else None
            if not result.get("has_next") or new_count == 0 or cursor is None:
                break
            direction = "NEXT"
            await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
        return series_item, sorted(episodes.values(), key=lambda e: e.order_value)

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
    zip은 임시 이름으로 완성한 뒤 한 번에 이름을 바꿔서, 도중에 끊겨도 반쪽짜리가 남지 않는다."""
    assert episode.number is not None
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
    return final_path


async def run_download(
    client: KakaoPageClient, *, series_id: int, title: str, start_no: int, last_downloaded_no: int,
    download_root: str, max_episodes: int, on_progress=None,
) -> RunResult:
    """작품 하나에서 지금 받을 수 있는 회차를 순서대로 최대 max_episodes개 받는다. 실패하면 그 회차에서 멈춘다."""
    listing = await client.list_episodes(series_id)
    if listing is None:
        return RunResult([], None, DownloadPlan(), "회차 목록을 가져오지 못했습니다.")
    _, episodes = listing
    folder = Path(download_root) / remove_forbidden_str_kakao(title)
    plan = plan_downloads(
        episodes, start_no=start_no, last_downloaded_no=last_downloaded_no, existing_numbers=scan_existing_numbers(folder)
    )
    downloaded: list[int] = []
    failed: int | None = None
    for episode in plan.to_download[:max_episodes]:
        path = await download_episode(client, series_id, episode, folder)
        if path is None:
            failed = episode.number
            if on_progress:
                on_progress(f"{episode.number}화 받기 실패 — 여기서 멈춥니다(다음에 이 회차부터 다시 시도)")
            break
        downloaded.append(episode.number)
        if on_progress:
            on_progress(f"{episode.number}화 받음 ({path.name})")
    return RunResult(downloaded, failed, plan)
