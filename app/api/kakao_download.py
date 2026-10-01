"""카카오페이지 다운로드: 로그인 쿠키, 수동 다운로드(분석/검색/받기)."""


import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app import (
    download_roots,
    job_status,
    manual_download,
    repository,
)
from app import kakao_api
from app import kakao_page_auth
from app import kakao_page_download
from app.config import get_settings



log = logging.getLogger(__name__)

router = APIRouter()



class KakaoPageLoginIn(BaseModel):
    cookies_json: str


_kakao_manual_task: asyncio.Task | None = None  # 진행 중 작업이 중간에 정리되지 않게 참조를 잡아둔다


@router.get("/settings/kakao-page-login")
async def get_kakao_page_login():
    return kakao_page_auth.cookie_status(await asyncio.to_thread(kakao_page_auth.load_cookies))


@router.post("/settings/kakao-page-login")
async def set_kakao_page_login(payload: KakaoPageLoginIn):
    try:
        cookies = kakao_page_auth.parse_cookie_export(payload.cookies_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await asyncio.to_thread(kakao_page_auth.save_cookies, cookies)
    return kakao_page_auth.cookie_status(cookies)


@router.delete("/settings/kakao-page-login")
async def delete_kakao_page_login():
    await asyncio.to_thread(kakao_page_auth.delete_cookies)
    return kakao_page_auth.cookie_status(None)


_KAKAO_LOGIN_CHECK_MESSAGES = {
    True: "로그인되어 있습니다.",
    False: "로그인이 풀려 있습니다. 쿠키를 다시 export해서 붙여넣어 주세요.",
    None: "카카오페이지에 연결하지 못했거나 요청이 막혀서 로그인 상태를 확인하지 못했습니다. 잠시 뒤 다시 시도해주세요.",
}


@router.post("/settings/kakao-page-login/check")
async def check_kakao_page_login():
    """저장된 쿠키로 실제로 로그인이 되는지 확인한다. 풀려 있으면(또는 만료가 며칠 안 남았으면) 디스코드로 알린다."""
    settings = get_settings()
    async with kakao_page_download.new_session() as session:
        client = kakao_page_download.client_from_saved_cookies(session, settings.request_timeout_seconds)
        if client is None:
            raise HTTPException(status_code=400, detail="저장된 카카오페이지 쿠키가 없습니다. 먼저 쿠키를 붙여넣어 저장해주세요.")
        logged_in = await client.check_login()
        kakao_page_download.persist_refreshed_cookies(client)
        status = kakao_page_auth.cookie_status(kakao_page_auth.load_cookies())
        await kakao_page_auth.notify_if_needed(session, settings, logged_in, status["days_left"])
    return {**status, "logged_in": logged_in, "message": _KAKAO_LOGIN_CHECK_MESSAGES[logged_in]}


class KakaoManualRunIn(BaseModel):
    series_id: int
    numbers: list[int]

    @field_validator("numbers")
    @classmethod
    def not_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("다운로드할 회차를 하나 이상 선택해주세요.")
        return v


def _kakao_series_status_label(item: dict) -> str:
    return "완결" if item.get("is_finished") else "휴재" if item.get("is_paused") else "연재"


@router.get("/kakao-manual/search")
async def kakao_manual_search(query: str):
    """제목/작가로 카카오페이지 웹툰 후보를 찾는다(완결/휴재작도 나옴)."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="검색할 제목을 입력해주세요.")
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        items = await kakao_api.search_series(session, query.strip(), settings.request_timeout_seconds)
    tracked_map = await asyncio.to_thread(repository.get_kakao_webtoons_map)
    return [
        {
            "title_id": item["title_id"], "title": item["title_name"], "thumbnail_url": item["thumbnail_url"],
            "authors": ", ".join(item["author_names"]), "status": _kakao_series_status_label(item),
            # 이 프로그램의 구독 상태(active 등, 없으면 None) — 카드가 이미 구독한 작품을 "구독"으로 보여주지 않게
            "subscription": (tracked_map.get(item["title_id"]) or {}).get("status"),
        }
        for item in items[:10]
    ]


def _can_open_with_waitfree(episode, waitfree_ready: bool) -> bool:
    return waitfree_ready and not episode.accessible and not episode.waitfree_blocked


def _kakao_episode_state(episode, waitfree_ready: bool) -> str:
    return "waitfree" if _can_open_with_waitfree(episode, waitfree_ready) else kakao_page_download.episode_state(episode)


@router.get("/kakao-manual/analyze")
async def kakao_manual_analyze(series_id: int):
    """작품의 회차를 폴더 규칙(폴더 없음 / 누락 회차 비교 / 파일 1개면 그 이후부터)으로 분석해서 표로 보여줄 결과를
    돌려준다. 아무것도 받지 않는다."""
    settings = get_settings()
    root = await asyncio.to_thread(download_roots.kakao_root, settings)
    async with kakao_page_download.new_session() as session:
        client = kakao_page_download.client_or_anonymous(session, settings.request_timeout_seconds)
        cookie_saved = bool(client.cookies)  # 저장된 쿠키가 있는 클라이언트만 쿠키 값을 갖는다(익명은 빈 값)
        logged_in: bool | None = None
        if cookie_saved:
            logged_in = await client.check_login()
            status = kakao_page_auth.cookie_status(kakao_page_auth.load_cookies())
            await kakao_page_auth.notify_if_needed(session, settings, logged_in, status["days_left"])
        listing = await client.list_episodes(series_id)
        # 대여권/소장권/기다무 현황(작품 화면의 "대여권 N장 | 소장권 N장 | 기다무"와 같은 값) — 로그인돼 있을 때만 의미가 있다
        tickets = await client.ticket_info(series_id) if cookie_saved and logged_in is not False else None
        kakao_page_download.persist_refreshed_cookies(client)
    if listing is None:
        raise HTTPException(status_code=502, detail="카카오페이지에서 회차 목록을 가져오지 못했습니다. 작품 번호를 확인하거나 잠시 뒤 다시 시도해주세요.")
    series_item, episodes = listing
    supported = kakao_page_download.waitfree_supported(series_item)
    waitfree_ready = supported and bool(tickets and tickets.waitfree_ready)  # 기다무가 없는 작품이면 이용권 응답이 "사용 가능"이어도 쓸 수 없다
    tracked = await asyncio.to_thread(repository.get_kakao_webtoon, series_id)
    title = series_item.get("title") or str(series_id)
    folder = kakao_page_download.series_folder(root, title)
    existing = await asyncio.to_thread(kakao_page_download.scan_existing_files, folder)
    plan = kakao_page_download.plan_by_folder_rules(episodes, existing, series_item.get("title") or "")
    return {
        "series_id": series_id, "title": title, "folder": str(folder), "mode": plan.mode, "existing_count": plan.existing_count,
        "marker": None if plan.marker is None else {
            "number": plan.marker.number, "subtitle": plan.marker.subtitle,
            "resolved_number": plan.marker.resolved_number, "warning": plan.marker.warning,
        },
        "to_download_count": len(plan.to_download), "locked_count": len(plan.locked),
        "downloaded_count": sum(1 for row in plan.rows if row.downloaded),
        "before_start_count": sum(1 for row in plan.rows if row.before_start),
        "cookie_saved": cookie_saved, "logged_in": logged_in,
        # 진단: 사이트가 말하는 전체 회차 수와 우리가 가져온 수(동영상으로 뺀 수 포함) — 목록에서 회차가 빠졌을 때 화면이 알려 준다
        "site_total": int(series_item.get("on_sale_count") or 0), "listed_count": len(episodes),
        "excluded_video_count": int(series_item.get("_excluded_video_count") or 0),
        "thumbnail_url": kakao_api._thumbnail_url(series_item), "authors": series_item.get("authors") or "",
        "subscription": tracked["status"] if tracked else None,  # 이 프로그램의 구독 상태(active 등, 없으면 None)
        "tickets": None if tickets is None else {
            "rental_count": tickets.rental_count, "own_count": tickets.own_count,
            "waitfree_supported": supported,  # False면 화면은 기다무 항목을 보여주지 않는다
            "waitfree_ready": tickets.waitfree_ready, "waitfree_available_at": tickets.waitfree_available_at,
            # 기다무 충전 주기(3시간/1일/3일 등 작품마다 다름) — 이용권 응답에 없으면 작품 정보의 값을 쓴다
            "waitfree_period_minutes": tickets.waitfree_period_minutes or int(series_item.get("waitfree_period_by_minute") or 0),
        },
        "episodes": [
            {
                "number": row.episode.number, "subtitle": row.episode.subtitle,
                # 잠긴 회차라도 기다무를 쓸 수 있으면 "waitfree" — 골라서 받으면 기다무로 열고 받는다(한 장이라 하나만)
                "state": _kakao_episode_state(row.episode, waitfree_ready), "expire": row.episode.rent_expire,
                "free_at": kakao_page_download.free_date_of(row.episode),  # 연재무료: 이 날짜에 무료가 된다
                "downloaded": row.downloaded, "before_start": row.before_start,
                "selectable": row.episode.accessible or _can_open_with_waitfree(row.episode, waitfree_ready),  # 이미 받은 회차도 다시 받을 수 있다(수동)
            }
            for row in plan.rows
        ],
    }


async def _run_kakao_manual_download(series_id: int, numbers: list[int]) -> None:
    job = manual_download.JOB_NAME
    settings = get_settings()

    def log_line(text: str) -> None:
        job_status.log_line(job, text)

    success = False
    try:
        async with kakao_page_download.download_lock:
            root = await asyncio.to_thread(download_roots.kakao_root, settings)
            async with kakao_page_download.new_session() as session:
                client = kakao_page_download.client_from_saved_cookies(session, settings.request_timeout_seconds)
                if client is None:
                    log_line("저장된 카카오페이지 쿠키가 없습니다. 설정에서 쿠키를 먼저 저장해주세요.")
                    return
                log_line(f"카카오페이지 series_id={series_id} — 로그인 확인 중...")
                logged_in = await client.check_login()
                status = kakao_page_auth.cookie_status(kakao_page_auth.load_cookies())
                await kakao_page_auth.notify_if_needed(session, settings, logged_in, status["days_left"])
                if logged_in is False:
                    log_line("카카오페이지 로그인이 풀려 있어서 받지 않았습니다. 쿠키를 다시 export해서 붙여넣어 주세요.")
                    return
                log_line(f"{len(numbers)}개 회차 다운로드 시작 (저장 폴더: {root})")
                result = await kakao_page_download.download_selected(
                    client, series_id=series_id, title=None, numbers=numbers, download_root=root, on_progress=log_line
                )
                kakao_page_download.persist_refreshed_cookies(client)
        for number, subtitle in result.downloaded_items:
            repository.add_episode_history(str(series_id), result.title, number, subtitle, "success", platform="kakao")
        for number in result.failed:
            repository.add_episode_history(str(series_id), result.title, number, "", "failed", "이미지 받기 실패", platform="kakao")
        log_line(
            f"[{result.title}] 받음 {len(result.downloaded)}개(그 중 다시 받아 교체 {len(result.replaced)}개) / "
            f"실패 {len(result.failed)}개 / 잠겨서 건너뜀 {len(result.skipped_locked)}개"
            + (f" / 기다무로 연 회차 {len(result.ticket_used)}개" if result.ticket_used else "")
        )
        success = not result.failed and not result.not_found
    except Exception as e:
        log.exception("카카오페이지 수동 다운로드 중 예외")
        log_line(f"예기치 못한 오류: {e}")
    finally:
        log_line("수동 다운로드 종료")
        job_status.finish(job, success=success)


@router.post("/kakao-manual/run")
async def kakao_manual_run(payload: KakaoManualRunIn):
    """분석 표에서 고른 회차를 받는다(백그라운드 — 진행은 기존 수동 다운로드 진행상황 창에 나온다)."""
    global _kakao_manual_task
    if await asyncio.to_thread(kakao_page_auth.load_cookies) is None:
        raise HTTPException(status_code=400, detail="카카오페이지 로그인 쿠키가 없습니다. 설정에서 쿠키를 먼저 저장해주세요.")
    if job_status.snapshot().get(manual_download.JOB_NAME, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="이미 수동 다운로드가 진행 중입니다.")
    if kakao_page_download.download_lock.locked():
        raise HTTPException(status_code=409, detail="카카오페이지 자동 다운로드가 진행 중입니다. 끝난 뒤 다시 시도해주세요.")
    job_status.start(manual_download.JOB_NAME)
    _kakao_manual_task = asyncio.create_task(_run_kakao_manual_download(payload.series_id, payload.numbers))
    return {"status": "started"}
