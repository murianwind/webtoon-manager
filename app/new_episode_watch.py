"""구독해제 웹툰의 새 에피소드 확인 — 신작 스캔(discovery job) 때 네이버/카카오 구독해제 작품에 새 회차가 올라왔는지 보고 디스코드로 알린다.

- 네이버: 가장 최근 회차 번호(`naver_api.fetch_latest_episode`)가 마지막으로 확인한 번호보다 커지면 새 에피소드.
- 카카오: 작품 정보의 회차 수(`on_sale_count`, 요청 한 번)가 마지막으로 확인한 수보다 늘면 새 에피소드.
- 처음 확인하는 작품은 알리지 않고 기준만 기록한다(안 그러면 구독해제 작품 전부에 알림이 한꺼번에 간다).
- 알림은 한 메시지로 묶어 보내고, 전송에 성공했을 때만 기준을 갱신한다(웹훅이 없거나 실패하면 다음 스캔에서 다시 알린다).
"""
import asyncio
import logging
from dataclasses import dataclass

import aiohttp

from app import discord_notify, job_status, kakao_page_download, naver_api, repository
from app.config import Settings
from app.cookie_loader import get_adult_cookies

log = logging.getLogger(__name__)

_HEADER = "🔔 **구독해제 웹툰 새 에피소드**"


@dataclass
class NewEpisode:
    platform: str  # "네이버" | "카카오" — 메시지에 보이는 이름
    title_id: str | int
    title: str
    marker: int  # 알림을 보낸 뒤 기준으로 기록할 값(네이버: 최신 회차 번호, 카카오: 회차 수)
    detail: str  # 메시지에 보일 내용


def decide(seen: int | None, latest: int | None) -> tuple[bool, int | None]:
    """(알릴지, 기록할 새 기준 또는 None). 처음 확인(기준 없음)이면 알리지 않고 기준만 기록하고, 값을 못 가져왔거나 그대로/줄었으면 아무것도 안 한다."""
    if latest is None:
        return False, None
    if seen is None:
        return False, latest
    if latest > seen:
        return True, latest
    return False, None


async def scan_naver(session: aiohttp.ClientSession, settings: Settings) -> list[NewEpisode]:
    """구독해제 네이버 웹툰마다 최신 회차를 확인한다. 기준만 기록하는 작품은 바로 기록하고, 알릴 작품만 돌려준다(기록은 전송 성공 뒤)."""
    targets = repository.list_by_status(repository.STATUS_UNSUBSCRIBED)
    cookies = get_adult_cookies(settings.cookie_file_path) if targets else {}
    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)

    async def _one(wt) -> NewEpisode | None:
        async with semaphore:
            try:
                episode = await naver_api.fetch_latest_episode(
                    session, wt.title_id, settings.request_timeout_seconds, cookies=cookies if wt.is_adult else None,
                )
            except Exception as e:
                log.error("구독해제 웹툰(titleId=%s) 최신 회차 확인 중 예외 — 건너뜁니다: %s", wt.title_id, e)
                return None
            finally:
                await asyncio.sleep(settings.delay_seconds)
        notify, record = decide(wt.new_episode_seen_no, episode.episode_no if episode else None)
        if notify:
            detail = f"{episode.episode_no}화 '{episode.subtitle}'" if episode.subtitle else f"{episode.episode_no}화"
            return NewEpisode("네이버", wt.title_id, wt.title, episode.episode_no, detail)
        if record is not None:
            repository.set_naver_new_episode_seen(wt.title_id, record)
        return None

    return [item for item in await asyncio.gather(*(_one(wt) for wt in targets)) if item is not None]


async def scan_kakao(client: kakao_page_download.KakaoPageClient, settings: Settings) -> list[NewEpisode]:
    """구독해제 카카오 웹툰마다 작품 정보의 회차 수를 확인한다(작품마다 요청 한 번, 사이에 딜레이)."""
    items: list[NewEpisode] = []
    for row in repository.list_kakao_webtoons_by_status(repository.STATUS_UNSUBSCRIBED):
        series_id = row["title_id"]
        try:
            series_item = await client.fetch_series_item(series_id)
        except Exception as e:
            log.error("구독해제 카카오 웹툰(series_id=%s) 작품 정보 확인 중 예외 — 건너뜁니다: %s", series_id, e)
            series_item = None
        await asyncio.sleep(settings.delay_seconds)
        count = int((series_item or {}).get("on_sale_count") or 0) or None
        notify, record = decide(row["new_episode_seen_no"], count)
        if notify:
            items.append(NewEpisode("카카오", series_id, row["title"], count, f"새 회차가 올라왔습니다 (현재 {count}회차)"))
        elif record is not None:
            repository.set_kakao_new_episode_seen(series_id, record)
    return items


def format_message(items: list[NewEpisode]) -> str:
    return "\n".join([_HEADER, *(f"[{item.platform}/{item.title}] {item.detail}" for item in items)])


def record_seen(item: NewEpisode) -> None:
    if item.platform == "카카오":
        repository.set_kakao_new_episode_seen(item.title_id, item.marker)
    else:
        repository.set_naver_new_episode_seen(item.title_id, item.marker)


async def run(session: aiohttp.ClientSession, settings: Settings) -> int:
    """네이버 + 카카오 구독해제 웹툰을 확인하고 새 에피소드가 있으면 한 메시지로 알린다. 알린 작품 수를 돌려준다."""
    items = await scan_naver(session, settings)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as kakao_session:
        client = kakao_page_download.client_from_saved_cookies(kakao_session, settings.request_timeout_seconds)
        if client is None:
            job_status.log_line("discovery", "카카오페이지 로그인 쿠키가 없어서 카카오 구독해제 웹툰의 새 에피소드 확인은 건너뜁니다")
        else:
            items += await scan_kakao(client, settings)
            kakao_page_download.persist_refreshed_cookies(client)
    if not items:
        return 0
    if not await discord_notify.send_webhook_notification(session, settings, format_message(items)):
        job_status.log_line("discovery", f"구독해제 웹툰 새 에피소드 {len(items)}건을 알리지 못했습니다(웹훅 없음/전송 실패) — 다음 스캔 때 다시 시도합니다")
        return 0
    for item in items:
        record_seen(item)
    return len(items)
