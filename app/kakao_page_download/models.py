"""카카오페이지 다운로드 — 상수, 데이터 모양, 폴더 규칙(받을 회차 정하기), 이름 규칙, 회차 판정(무료/대여/만료). 네트워크와 파일 쓰기가 없는 부분이다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import kakao_api, repository
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


WAITFREE_TICKET_TYPE = "RT05"  # 기다무 대여권


RENTAL_TICKET_TYPE = "RT01"  # 일반 대여권(작품마다 구매해 둔 것, 3일 대여) — 기다무와 이 둘만 쓴다. 선물권(RT06) 등은 쓰지 않고, 결제/구매는 아예 없다


_IMAGE_SLIDE_TYPE = "SD03"  # 이미지 회차(그 밖의 종류는 동영상 등이라 받지 않는다)


_TRIAL_TITLE_WORD = "체험판"  # 제목에 이 단어가 있는 회차는 받지 않는다


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
    free_change_dt: str | None = None  # 무료로 바뀌는 시각(연재무료 작품의 "N일 후 무료") — 이미 무료인 회차는 아주 옛 날짜가 온다


@dataclass
class TicketInfo:
    """작품의 이용권 현황 — 카카오페이지 작품 화면의 "대여권 N장 | 소장권 N장 | 기다무" 표시와 같은 값."""

    rental_count: int = 0  # 대여권(선물권 등)
    own_count: int = 0  # 소장권
    waitfree_ready: bool = False  # 지금 기다무를 쓸 수 있음
    waitfree_available_at: str | None = None  # 기다무를 다시 쓸 수 있게 되는 시각(쓸 수 없을 때)
    waitfree_period_minutes: int = 0  # 기다무가 다시 충전되는 주기(작품마다 3시간/1일/3일 등으로 다르다)


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
    downloaded: bool  # 이미 받음 — 폴더에 파일이 있거나(번호 또는 부제목이 같음), 받은 회차 기록에 있음
    before_start: bool = False  # 폴더/기록의 가장 이른 회차보다 앞 — 자동으로는 받지 않는다(수동으로는 받을 수 있다)
    archived: bool = False  # 받은 회차 기록에는 있는데 폴더에는 없음 — 아카이빙으로 보관 폴더에 옮겨진 회차


@dataclass
class DownloadPlan:
    mode: str  # new_folder | single_marker | compare
    rows: list[EpisodeRow] = field(default_factory=list)
    to_download: list[Episode] = field(default_factory=list)  # 지금 받을 수 있는 것(번호순) — 앞에 잠긴 회차가 있어도 무료/대여 중이면 포함
    locked: list[Episode] = field(default_factory=list)  # 못 받는 것 전부(번호순) — 가장 앞의 잠긴 회차부터 기다무로 하나씩 연다
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
    finished: bool = False  # 작품이 완결로 표시돼 있음(작품 정보의 on_issue == "N")
    waitfree_supported: bool = True  # 이 작품에 기다무가 있음(작품 정보의 is_waitfree)
    rental_used: list[int] = field(default_factory=list)  # 일반 대여권(RT01)으로 열어서 받은 회차 번호
    rental_remaining: int | None = None  # 이 작품에 남은 대여권 수(대여권을 쓰려고 확인한 경우에만 안다)
    rental_skip_mode: bool = False  # 대여권 자동 사용이 켜진 실행(기다무 대기 회차를 건너뛰고 대여권 회차를 본다)

    @property
    def caught_up_episode(self) -> "Episode | None":
        """연재 중인 작품을 최신 회차까지 다 받아서 더 받을 회차가 없을 때, 그 마지막 회차(아니면 None). 완결작은 해당하지 않는다 —
        완결 확인 알림이 따로 있다."""
        if self.finished or not self.nothing_left or not self.plan.rows:
            return None
        return max((row.episode for row in self.plan.rows), key=lambda episode: episode.number)

    def _needs_ticket(self, episode: "Episode") -> bool:
        """이 잠긴 회차를 열려면 대여권이 필요한가 — 기다무로 못 여는 회차(기다무가 없는 작품이거나 기다무 불가 회차). 숨김/"N일 후 무료" 회차는 기다리면 열리므로 제외."""
        return not episode.hidden and not free_date_of(episode) and (not self.waitfree_supported or episode.waitfree_blocked)

    def _opened_numbers(self) -> set[int]:
        return set(self.rental_used) | ({self.ticket_used} if self.ticket_used is not None else set())

    @property
    def ticket_needed(self) -> "Episode | None":
        """받을 회차를 다 받았는데 대여권이 필요해서 더 못 받는 경우, 그 회차(없으면 None). 오류/실패가 있거나 받을 회차가 남았으면 아직 판단하지 않는다.
        - 대여권 자동 사용이 꺼진 실행: 잠긴 회차 중 가장 앞의 것이 대여권이 필요한 회차일 때(기다무로 열 수 있는 회차 = 충전 대기나 "N일 후 무료"는 기다리면
          자동으로 이어지므로 해당하지 않는다).
        - 켜진 실행: 기다무 대기 회차는 건너뛰므로, 대여권으로 열려고 했는데 보유 대여권이 0장이어서 못 연 첫 회차(보유 수를 확인한 적이 없거나 아직 남았으면 단정하지 않는다)."""
        if self.error is not None or self.failed is not None:
            return None
        opened = self._opened_numbers()
        if len(self.downloaded) - len(opened) < len(self.plan.to_download):
            return None
        remaining = [episode for episode in self.plan.locked if episode.number not in opened]
        if self.rental_skip_mode:
            if self.rental_remaining != 0:
                return None
            return next((episode for episode in remaining if self._needs_ticket(episode)), None)
        if not remaining:
            return None
        return remaining[0] if self._needs_ticket(remaining[0]) else None

    @property
    def waiting_for_waitfree(self) -> int:
        """기다무 충전이나 "N일 후 무료"를 기다리는 잠긴 회차 수(열린 회차와 대여권이 필요한 회차는 뺀다) — 실행 요약에 쓴다."""
        opened = self._opened_numbers()
        return sum(1 for episode in self.plan.locked if episode.number not in opened and not self._needs_ticket(episode))

    @property
    def nothing_left(self) -> bool:
        """이번 실행 뒤 자동으로 받을 회차가 더 남지 않았다 — 실패/오류가 없고, 받을 수 있던 회차를 다 받았고, 잠긴 회차가 없다
        (기다무/대여권으로 연 회차는 잠긴 회차 중 하나였으므로 뺀다). 완결 확인 알림은 이때만 보낸다."""
        if self.error is not None or self.failed is not None:
            return False
        opened = self._opened_numbers()  # 기다무/대여권으로 막 열어 받은 회차는 잠긴 회차 중 하나였으므로 뺀다
        remaining_locked = sum(1 for episode in self.plan.locked if episode.number not in opened)
        return remaining_locked <= 0 and len(self.downloaded) - len(opened) >= len(self.plan.to_download)


@dataclass
class SelectedResult:
    downloaded: list[int] = field(default_factory=list)
    downloaded_items: list[tuple[int, str]] = field(default_factory=list)
    replaced: list[int] = field(default_factory=list)  # 이미 받은 회차를 다시 받아 교체한 것(downloaded의 일부)
    failed: list[int] = field(default_factory=list)
    skipped_locked: list[int] = field(default_factory=list)
    ticket_used: list[int] = field(default_factory=list)  # 기다무로 열어서 받은 회차
    rental_used: list[int] = field(default_factory=list)  # 일반 대여권(RT01)으로 열어서 받은 회차
    rental_remaining: int | None = None  # 이 작품에 남은 대여권 수(대여권을 써 본 경우에만 안다)
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


def _subtitle_key(subtitle: str, series_title: str) -> str:
    """파일에 적힌 부제목을 사이트 부제목과 비교할 수 있는 형태로 — 예전 도구가 받은 파일은 "탈조클럽 31화"처럼 작품명이 앞에
    붙어 있는데 사이트 부제목은 작품명을 뺀 "31화"라서, 같은 치환을 거친 작품명이 앞에 붙어 있으면 떼고 비교한다."""
    text = normalize_subtitle(subtitle)
    prefix = normalize_subtitle(series_title)
    if prefix and text.startswith(prefix) and text[len(prefix):].strip():
        return text[len(prefix):].strip()
    return text


def plan_by_folder_rules(
    episodes: list[Episode], existing: list[ExistingFile], series_title: str = "", recorded: set[int] | None = None, pending: set[int] | None = None
) -> DownloadPlan:
    """폴더 규칙(모듈 설명 참고)과 "받은 회차 기록"으로 받을 회차를 정한다.

    받을 수 있는 회차(무료/대여 중)는 앞에 잠긴 회차가 있어도 그때그때 받고, 잠긴 회차는 번호순으로 모아 두었다가 가장 앞의 것부터
    기다무로 하나씩 연다(run_download). recorded는 작품의 받은 회차 기록(없으면 None) — 폴더에 파일이 있고 기록도 있으면 아카이빙으로
    옮겨진 회차도 "받은 것"으로 보고(파일이 하나뿐이라는 이유로 앞 회차가 전부 잘려 나가지 않는다), 가장 앞 기록부터를 대상으로 삼는다.
    폴더에 파일이 없으면 기록은 무시하고 처음부터 받는다(폴더를 비운 것은 다시 받으려는 것). series_title은 파일 이름 속 부제목에 붙은
    작품명을 떼고 비교하는 데 쓴다(없으면 있는 그대로 비교).

    숨김(hidden) 회차는 "아직 무료/대여로 열리지 않은 예정 회차"다 — 사이트는 "N일 후 무료"로 목록에 보여 주므로 빼지 않고 잠긴 회차로
    센다(화면에는 무료가 되는 날짜가 보인다). 받을 수는 없다."""
    visible = sorted((e for e in episodes if e.number > 0), key=lambda e: e.number)
    pending = pending or set()  # 사용자가 "받지 않은 것으로" 기록한 회차 — 시작 지점보다 앞이어도 받을 대상(받은 것이 이긴다)
    marker: MarkerInfo | None = None
    folder_numbers = {f.number for f in existing}
    archived_numbers: set[int] = set()

    lower_bound = 0  # 이 번호보다 앞 회차는 자동으로 받지 않는다
    if not existing:
        mode = "new_folder"
    elif recorded:
        mode = "compare"
        archived_numbers = recorded - folder_numbers
        lower_bound = min(folder_numbers | recorded)
    elif len(existing) == 1:
        mode = "single_marker"
        only = existing[0]
        resolved, warning = only.number, False
        only_key = _subtitle_key(only.subtitle, series_title)
        at_number = next((e for e in visible if e.number == only.number), None)
        if at_number is None or normalize_subtitle(at_number.subtitle) != only_key:
            # 번호가 사이트와 안 맞으면 부제목으로 위치를 다시 찾는다(예전 도구와 번호가 밀려 있는 경우)
            same_title = [e for e in visible if normalize_subtitle(e.subtitle) == only_key]
            if len(same_title) == 1:
                resolved = same_title[0].number
            else:
                warning = True
        marker = MarkerInfo(number=only.number, subtitle=only.subtitle, resolved_number=resolved, warning=warning)
        lower_bound = resolved
    else:
        mode = "compare"
        lower_bound = min(folder_numbers)

    have_numbers = folder_numbers | archived_numbers
    have_subtitles = {_subtitle_key(f.subtitle, series_title) for f in existing}
    plan = DownloadPlan(mode=mode, marker=marker, existing_count=len(existing))
    for episode in visible:
        in_folder = episode.number in folder_numbers or normalize_subtitle(episode.subtitle) in have_subtitles
        done = in_folder or episode.number in have_numbers
        skipped = not done and episode.number < lower_bound and episode.number not in pending
        plan.rows.append(EpisodeRow(episode=episode, downloaded=done, before_start=skipped, archived=done and not in_folder))
        if done or skipped:
            continue
        if episode.accessible and not episode.hidden:
            plan.to_download.append(episode)
        else:
            plan.locked.append(episode)
    return plan


def effective_download_root(default_root: str) -> str:
    """카카오페이지 전용 다운로드 폴더(설정)가 있으면 그것을, 없으면 기본 다운로드 폴더를 쓴다."""
    return repository.get_setting(DOWNLOAD_ROOT_SETTING_KEY) or default_root


def series_folder(download_root: str, title: str) -> Path:
    return Path(download_root) / remove_forbidden_str_kakao(title)


_warned_expire_formats: set[str] = set()  # 읽지 못한 만료일의 "모양"(숫자를 9로 바꾼 것) — 같은 모양은 경고를 한 번만 남긴다


def _parse_expire(value: str) -> datetime | None:
    """rent_expire_dt를 한국 시각 기준(시간대 없는) datetime으로. 서버가 실제로 주는 ISO 8601("2026-10-12T01:46:22+09:00", Z 포함)과
    예전 형식("2026-10-12 01:46:22", 초 없는 "… 01:46")을 읽는다. 못 읽으면 None."""
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_KST).replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def _is_accessible(item: dict, now_kst: datetime) -> bool:
    if item.get("is_free"):
        return True
    purchase = ((item.get("service_property") or {}).get("purchase_info")) or {}
    if purchase.get("purchase_type") not in _ACCESSIBLE_PURCHASE_TYPES:
        return False
    expire = purchase.get("rent_expire_dt")
    if not expire:
        return True
    expires_at = _parse_expire(expire)
    if expires_at is None:
        # 읽을 수 없는 형식이면 "아직 대여 중"으로 믿지 않는다 — 만료된 회차를 받으려다 실패하며 작품이 멈추는 것보다 받을 수 없는 회차로 보는 편이 안전하다
        shape = re.sub(r"\d", "9", str(expire))
        if shape not in _warned_expire_formats:
            _warned_expire_formats.add(shape)
            log.warning("카카오페이지 대여 만료일을 읽지 못해 받을 수 없는 회차로 봅니다: %r (product_id=%s)", expire, item.get("product_id"))
        return False
    return expires_at > now_kst


def is_video_item(item: dict) -> bool:
    """이미지가 아닌 회차(동영상 트레일러 등). slide_type이 이미지(SD03)가 아니거나 제목에 "동영상"이 있으면 동영상으로 본다."""
    slide_type = item.get("slide_type")
    if slide_type and slide_type != _IMAGE_SLIDE_TYPE:
        return True
    return "동영상" in (item.get("title") or "")


def is_trial_item(item: dict) -> bool:
    """제목에 "체험판"이 들어간 회차(맛보기용 회차)."""
    return _TRIAL_TITLE_WORD in (item.get("title") or "")


def is_excluded_item(item: dict) -> bool:
    """받지 않고 목록에서 빼는 회차 — 동영상과 체험판. 둘 다 예전 도구가 번호를 주지 않았으므로 뒤 회차 번호를 당긴다."""
    return is_video_item(item) or is_trial_item(item)


def _parse_episode(item: dict, series_title: str, now_kst: datetime) -> Episode:
    title = item.get("title") or ""
    purchase = ((item.get("service_property") or {}).get("purchase_info")) or {}
    accessible = _is_accessible(item, now_kst) and not item.get("hidden")  # 숨김 회차는 받을 수 없다
    return Episode(
        product_id=item["product_id"], title=title, number=int(item.get("order_value") or 0),
        subtitle=derive_subtitle(title, series_title), is_free=bool(item.get("is_free")), accessible=accessible,
        page_count=int(item.get("page_count") or 0), hidden=bool(item.get("hidden")),
        rent_expire=purchase.get("rent_expire_dt") if accessible and not item.get("is_free") else None,
        waitfree_blocked=bool(item.get("waitfree_blocked")),
        free_change_dt=item.get("free_change_dt"),
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


def rental_eligible(episode: Episode, series_item: dict) -> bool:
    """일반 대여권으로 열어도 되는 잠긴 회차 — 기다무로 열 수 없는 회차(기다무가 없는 작품이거나 기다무 불가 회차). 기다무로 열 수 있는 회차(충전 대기)와
    "N일 후 무료"로 열릴 회차(숨김/무료 전환 예정)는 기다리면 열리므로 대여권을 쓰지 않는다."""
    if episode.accessible or episode.hidden or free_date_of(episode):
        return False
    return not waitfree_supported(series_item) or episode.waitfree_blocked


def free_date_of(episode: Episode, now: datetime | None = None) -> str | None:
    """아직 못 받는 회차가 무료로 열리는 날짜("YYYY-MM-DD", 한국시간) — 연재무료 작품의 "N일 후 무료". 이미 받을 수 있거나, 날짜가 지났거나,
    날짜를 모르면 None. now는 테스트에서 기준 시각을 고정하려고 받는다(시간대가 있는 시각)."""
    if episode.accessible or not episode.free_change_dt:
        return None
    try:
        when = datetime.fromisoformat(episode.free_change_dt).astimezone(_KST)
    except ValueError:
        return None
    return when.strftime("%Y-%m-%d") if when > (now or datetime.now(_KST)) else None


def waitfree_supported(series_item: dict) -> bool:
    """이 작품에 기다무(기다리면 무료)가 있는지. 이용권 응답(ticket/my)은 기다무가 없는 작품에도 기다무 항목을 채워 주므로(충전 주기 기본값
    등) 그걸로 판단하면 안 되고, 작품 정보의 is_waitfree를 본다(연재무료 작품 등은 False)."""
    return bool(series_item.get("is_waitfree"))
