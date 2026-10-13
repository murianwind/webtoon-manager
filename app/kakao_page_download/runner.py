"""카카오페이지 다운로드 실행 — 회차 하나 받기, 자동(번호순)/수동 다운로드, 기다무/대여권, info.xml/표지 쓰기.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zipfile
from pathlib import Path

from app import comicinfo, kakao_cover, kakao_records, repository
from app.kakao_authors import split_authors
from app.metadata_zip import COVER_MISSING, MetadataBundle

from app.kakao_page_download.client import (
    KakaoPageClient,
)
from app.kakao_page_download.models import (
    DownloadPlan,
    Episode,
    IMAGE_CONCURRENCY,
    RunResult,
    SelectedResult,
    TicketInfo,
    _image_extension,
    episode_zip_name,
    plan_by_folder_rules,
    rental_eligible,
    scan_existing_files,
    series_folder,
    waitfree_supported,
)

log = logging.getLogger(__name__)

# ── 받기 ─────────────────────────────────────────────────────────────────

def _failure_note(client) -> str:
    """받기 실패 로그 줄 뒤에 붙일 원인(없으면 빈 문자열)."""
    reason = getattr(client, "last_failure", None)
    return f" · 원인: {reason}" if reason else ""


async def download_episode(client: KakaoPageClient, series_id: int, episode: Episode, folder: Path) -> Path | None:
    """회차 하나를 zip으로 저장하고 경로를 돌려준다. 이미지 목록을 못 받거나 이미지가 하나라도 안 받히면
    아무것도 남기지 않고 None — 일부만 담긴 zip이 있으면 다음에 "이미 받은 회차"로 오인하기 때문이다.
    zip은 임시 이름으로 완성한 뒤 한 번에 이름을 바꿔서, 도중에 끊겨도 반쪽짜리가 남지 않는다.
    같은 번호의 예전 파일(이름이 다른 것, 예: 뒤에 #페이지수가 붙은 것)이 있으면 새 파일을 다 저장한 뒤에
    지운다 — 다시 받기를 하면 한 회차에 파일이 둘이 되지 않도록."""
    client.last_failure = None
    urls = await client.viewer_image_urls(series_id, episode.product_id)
    if not urls:
        return None

    # 첫 이미지로 먼저 확인한다. 서명된 주소가 안 먹으면(만료/무효) 주소를 새로 받아서 한 번 더 해 보고, 그래도 안 되면 나머지 수백
    # 장을 헛되이 요청하지 않고 바로 실패로 끝낸다.
    first = await client.download_image(urls[0])
    if first is None:
        urls = await client.viewer_image_urls(series_id, episode.product_id) or urls
        first = await client.download_image(urls[0])
        if first is None:
            return None

    semaphore = asyncio.Semaphore(IMAGE_CONCURRENCY)

    async def _one(url: str):
        async with semaphore:
            return await client.download_image(url)

    images = [first, *await asyncio.gather(*[_one(u) for u in urls[1:]])]
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
    repository.add_kakao_downloaded_numbers(series_id, [episode.number])  # 자동/수동/기다무가 모두 거치는 저장 지점 — 받은 회차 기록에 더한다
    repository.remove_kakao_pending_numbers(series_id, [episode.number])  # "받지 않은 것으로" 기록해 둔 회차를 받았으면 그 지정은 푼다
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
    use_rental_tickets: bool = False,
) -> RunResult:
    """폴더 규칙(자동 다운로드용)으로 받을 회차를 정해 번호순으로 최대 max_episodes개 받는다(기다무로 여는 회차도 자기 번호 차례에). 실패하면 그 회차에서 멈춘다.
    폴더 이름은 title(있으면), 없으면 카카오페이지에 등록된 작품 제목을 쓴다."""
    listing = await client.list_episodes(series_id)
    if listing is None:
        return RunResult([], [], None, DownloadPlan(mode="new_folder"), "회차 목록을 가져오지 못했습니다.")
    series_item, episodes = listing
    folder_title = title or series_item.get("title") or str(series_id)
    folder = series_folder(download_root, folder_title)
    existing = scan_existing_files(folder)
    plan = plan_by_folder_rules(episodes, existing, series_item.get("title") or "")
    recorded = kakao_records.sync_before_planning(series_id, existing, plan)  # 기록이 없으면 폴더+아카이빙 이력으로 처음 만들고, 폴더를 비웠으면 버린다
    pending = repository.get_kakao_pending_numbers(series_id)
    if recorded or pending:
        plan = plan_by_folder_rules(episodes, existing, series_item.get("title") or "", recorded, pending)
    downloaded: list[int] = []
    items: list[tuple[int, str]] = []
    failed: int | None = None
    ticket_used: int | None = None
    # 번호순으로 처리한다: 받을 수 있는 회차는 받고, 가장 앞의 잠긴 회차는 자기 번호 차례에 기다무로 연다(한 장뿐이라 그 뒤 잠긴 회차는 대기).
    # 지금 못 여는 잠긴 회차(기다무 충전 중이거나 기다무로 못 여는 회차)는 건너뛰고 뒤의 열린 회차를 받는다 — 뒤에 무료가 있어도 영원히 막히지 않게.
    # 받기에 실패하면 번호순을 지키려고 그 회차에서 멈춘다(다음에 이 회차부터 다시 시도).
    waitfree_target = plan.locked[0] if plan.locked and waitfree_supported(series_item) else None
    for episode in sorted([*plan.to_download, *([waitfree_target] if waitfree_target else [])], key=lambda e: e.number):
        if len(downloaded) >= max_episodes:
            break
        if episode is waitfree_target:
            ticket_used, failed = await _download_first_locked_with_waitfree(client, series_id, episode, folder, downloaded, items, on_progress)
            if failed is not None:
                break
            continue
        path = await download_episode(client, series_id, episode, folder)
        if path is None:
            failed = episode.number
            if on_progress:
                on_progress(f"{episode.number}번 회차 받기 실패 — 여기서 멈춥니다(다음에 이 회차부터 다시 시도)" + _failure_note(client))
            break
        downloaded.append(episode.number)
        items.append((episode.number, episode.subtitle))
        if on_progress:
            on_progress(f"{episode.number}번 회차 받음 ({path.name})")
    rental_used: list[int] = []
    rental_remaining: int | None = None
    if failed is None and use_rental_tickets and plan.locked:
        # 설정에서 켠 경우: 기다무로 못 여는 잠긴 회차(기다무 대기·"N일 후 무료" 회차는 건너뜀)를 이 작품의 보유 대여권 수까지 번호순으로 열어 받는다.
        # 이미 받은 회차는 plan.locked에 없으므로(폴더/기록에 있으면 제외) 대여가 끝나 다시 잠겨 보이는 회차에 또 쓰는 일은 없다. 기다무로 막 연 회차는 뺀다.
        candidates = [episode for episode in plan.locked if episode.number != ticket_used]
        rental_used, rental_remaining, failed = await _download_locked_with_rental(
            client, series_id, series_item, candidates, folder, downloaded, items, max_episodes, on_progress
        )
    if downloaded:
        await write_series_metadata(series_item, series_id, folder, on_progress, await client.fetch_about(series_id))
    return RunResult(
        downloaded, items, failed, plan, title=folder_title, ticket_used=ticket_used, finished=series_item.get("on_issue") == "N",
        waitfree_supported=waitfree_supported(series_item), rental_used=rental_used, rental_remaining=rental_remaining,
        rental_skip_mode=use_rental_tickets,
    )


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
            on_progress(f"{episode.number}번 회차 받기 실패 — 기다무로 이미 열렸으니 대여 기간 안에 다음 실행에서 다시 받습니다" + _failure_note(client))
        return episode.number, episode.number
    downloaded.append(episode.number)
    items.append((episode.number, episode.subtitle))
    if on_progress:
        on_progress(f"{episode.number}번 회차 받음 ({path.name})")
    return episode.number, None


async def _download_locked_with_rental(
    client, series_id, series_item, locked, folder, downloaded, items, max_episodes, on_progress
) -> tuple[list[int], int | None, int | None]:
    """(대여권으로 연 회차 번호들, 남은 대여권 수 또는 None, 받기에 실패한 회차 번호 또는 None). 잠긴 회차를 번호순으로 보되, 기다무로 열 수 있는 회차(충전 대기)나
    "N일 후 무료" 회차는 기다리면 열리므로 건너뛰고(대여권을 쓰지 않는다), 기다무로 못 여는 회차에만 쓴다. 대여권을 못 쓰거나 사용이 거절되면 이 작품은 거기서 멈춘다
    (보유 수는 작품마다 따로라서 뒤 회차도 같은 이유로 못 연다)."""
    used: list[int] = []
    remaining: int | None = None
    skipped_waiting = 0  # 첫 대여권 회차에 닿기 전에 건너뛴 회차 수(로그 한 줄로 알려 준다)
    attempted = False
    for episode in locked:
        if len(downloaded) >= max_episodes:
            break
        if not rental_eligible(episode, series_item):
            if not attempted:
                skipped_waiting += 1
            continue
        if not attempted and skipped_waiting and on_progress:
            on_progress(f"기다무로 열 수 있거나 N일 후 무료인 잠긴 회차 {skipped_waiting}개는 건너뛰고(기다리면 열립니다) 대여권이 필요한 회차부터 봅니다")
        attempted = True
        ok, reason, left = await client.use_rental_ticket(episode.product_id)
        if left is not None:
            remaining = left
        if not ok:
            if on_progress:
                on_progress(f"{episode.number}번 회차: {reason} — 이 작품의 대여권 사용은 여기서 멈춥니다")
            break
        used.append(episode.number)
        if on_progress:
            on_progress(f"{episode.number}번 회차를 대여권으로 열었습니다 (남은 대여권 {left}장)")
        path = await download_episode(client, series_id, episode, folder)
        if path is None:
            if on_progress:
                on_progress(f"{episode.number}번 회차 받기 실패 — 대여권으로 이미 열렸으니 대여 기간 안에 다음 실행에서 다시 받습니다" + _failure_note(client))
            return used, remaining, episode.number
        downloaded.append(episode.number)
        items.append((episode.number, episode.subtitle))
        if on_progress:
            on_progress(f"{episode.number}번 회차 받음 ({path.name})")
    return used, remaining, None


async def download_selected(
    client: KakaoPageClient, *, series_id: int, title: str | None, numbers: list[int], download_root: str, on_progress=None,
    use_rental_tickets: bool = False,
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
    plan = plan_by_folder_rules(
        episodes, scan_existing_files(folder), series_item.get("title") or "", repository.get_kakao_downloaded_numbers(series_id),
        repository.get_kakao_pending_numbers(series_id),
    )
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
            elif not waitfree_supported(series_item):
                reason = "이 작품은 기다무가 없습니다"
            elif row.episode.waitfree_blocked:
                reason = "기다무로 열 수 없는 회차입니다"
            else:
                ticket = ticket or await client.ticket_info(series_id) or TicketInfo()
                if ticket.waitfree_ready:
                    opened, reason = await client.use_waitfree_ticket(row.episode.product_id)
                    waitfree_spent = True
            rental_opened = False
            if not opened and use_rental_tickets and rental_eligible(row.episode, series_item):
                # 확인을 받은 경우에만: 기다무로 못 여는 회차를 대여권으로 연다(보유 수를 넘으면 나머지는 건너뜀)
                if result.rental_remaining == 0:
                    reason = "대여권이 부족합니다"
                else:
                    rental_opened, reason, left = await client.use_rental_ticket(row.episode.product_id)
                    if left is not None:
                        result.rental_remaining = left
                    opened = rental_opened
            if not opened:
                result.skipped_locked.append(number)
                if on_progress:
                    on_progress(f"{number}번 회차: {reason} — 건너뜀")
                continue
            if rental_opened:
                result.rental_used.append(number)
                if on_progress:
                    on_progress(f"{number}번 회차를 대여권으로 열었습니다 (남은 대여권 {result.rental_remaining}장)")
            else:
                result.ticket_used.append(number)
                if on_progress:
                    on_progress(f"{number}번 회차를 기다무로 열었습니다")
        path = await download_episode(client, series_id, row.episode, folder)
        if path is None:
            result.failed.append(number)
            if on_progress:
                on_progress(f"❌ {number}번 회차 받기 실패" + (" (기존 파일은 그대로 둡니다)" if row.downloaded and not row.archived else ""))
        else:
            result.downloaded.append(number)
            result.downloaded_items.append((number, row.episode.subtitle))
            replaced = row.downloaded and not row.archived  # 보관 폴더로 옮겨진 회차를 다시 받는 것은 폴더의 파일을 바꾸는 게 아니다
            if replaced:
                result.replaced.append(number)
            if on_progress:
                on_progress(f"✅ {number}번 회차 {'다시 받아 교체' if replaced else '완료'} ({path.name})")
    if result.downloaded:
        await write_series_metadata(series_item, series_id, folder, on_progress, await client.fetch_about(series_id))
    return result


def write_info_xml(series_item: dict, series_id: int, folder: Path, about: dict | None = None) -> None:
    """작품 폴더의 info.xml을 최신 정보로 (다시) 쓰고, 파일명 템플릿 {author}용 글 작가도 저장한다. 받을 때마다/메타 동기화 때마다 같은 규칙."""
    writers, _, originals = split_authors(about)
    repository.set_kakao_authors(series_id, writers, originals)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.xml").write_text(comicinfo.build_kakao_comicinfo_xml(series_item, series_id, about), encoding="utf-8")


async def build_series_metadata_bundle(client: "KakaoPageClient", series_id: int) -> MetadataBundle | None:
    """분석한 카카오페이지 웹툰의 info.xml과 공식 표지(cover.jpg)를 서버 폴더나 DB에 쓰지 않고 메모리로 만든다(브라우저 다운로드용 "표지·정보 받기").
    작품 정보를 못 받으면 None, 표지만 못 받으면 info.xml만 담고 warnings에 표시를 남긴다. write_info_xml과 달리 작가 정보 저장 같은 DB 쓰기는 하지 않는다."""
    series_item = await client.fetch_series_item(series_id)
    if series_item is None:
        return None
    about = await client.fetch_about(series_id)
    files = {"info.xml": comicinfo.build_kakao_comicinfo_xml(series_item, series_id, about).encode("utf-8")}
    warnings: list[str] = []
    cover = await asyncio.to_thread(kakao_cover.fetch_official_cover_bytes, str(series_id))
    if cover:
        files["cover.jpg"] = cover
    else:
        warnings.append(COVER_MISSING)
    return MetadataBundle(title=series_item.get("title") or str(series_id), files=files, warnings=warnings)


async def write_series_metadata(series_item: dict, series_id: int, folder: Path, on_progress=None, about: dict | None = None) -> None:
    """받은 뒤 작품 폴더에 info.xml을 쓰고(네이버와 같이 받을 때마다 최신 정보로 덮어씀), 표지(cover)가 없으면
    카카오페이지 공식 표지를 받아 저장한다. 실패해도 다운로드 결과에는 영향을 주지 않는다."""
    try:
        write_info_xml(series_item, series_id, folder, about)
        if not any(folder.glob("cover.*")):
            cover = await asyncio.to_thread(kakao_cover.fetch_official_cover_bytes, str(series_id))
            if cover:
                (folder / "cover.jpg").write_bytes(cover)
            elif on_progress:
                on_progress("표지 이미지를 받지 못했습니다(다음에 다시 시도)")
    except Exception as e:
        log.warning("카카오페이지 info.xml/표지 저장 실패 (%s): %s", folder, e)
