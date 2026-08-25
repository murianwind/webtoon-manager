"""
웹 페이지가 사용하는 REST API.

LAN 전용, 인증 없음. 입력값 검증 실패 시 크래시 대신 명확한 4xx 응답을 준다.

- 구독중/구독해제/제외됨 조회 및 상태 전환 : /webtoons/*
- 네이버 전체 웹툰 목록 조회 + 거기서 바로 구독/제외 : /naver-list/*
- 작가/태그 자동추가 레지스트리 관리 : /watched-authors/*, /watched-tags/*
- 수동 다운로드 : /manual-download/*
- 다운로드/스캔 스케줄 설정 : /settings
- 디스코드 설정 + 테스트 : /settings/discord*
- 백업/복원 : /backup, /restore
- 수동 실행 + 진행상황 조회 : /jobs/*

정렬/검색은 데이터 양이 많지 않아 서버에서 별도 파라미터를 두지 않고
프론트엔드에서 이미 받아온 목록을 가지고 처리한다 (요청 왕복을 줄임).
"""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import (
    discord_bot,
    discord_config,
    discord_notify,
    job_status,
    manual_download,
    naver_api,
    repository,
    schedule_config,
    tracker,
)
from app import scheduler as scheduler_mod
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class WebtoonOut(BaseModel):
    title_id: str
    title: str
    status: str
    is_adult: bool
    added_source: str
    last_downloaded_no: int
    is_finished: bool
    finish_ack: bool
    thumbnail_url: str
    genres: list[str]
    tags: list[str]
    latest_episode_no: int
    is_paused: bool
    has_new_episode: bool
    writer_ids: list[str]


def _to_out(wt) -> WebtoonOut:
    return WebtoonOut(
        title_id=wt.title_id,
        title=wt.title,
        status=wt.status,
        is_adult=wt.is_adult,
        added_source=wt.added_source,
        last_downloaded_no=wt.last_downloaded_no,
        is_finished=wt.is_finished,
        finish_ack=wt.finish_ack,
        thumbnail_url=wt.thumbnail_url,
        genres=wt.genres,
        tags=wt.tags,
        latest_episode_no=wt.latest_episode_no,
        is_paused=wt.is_paused,
        has_new_episode=wt.latest_episode_no > wt.last_downloaded_no > 0,
        writer_ids=wt.writer_ids,
    )


def _get_or_404(title_id: str):
    wt = repository.get(title_id)
    if wt is None:
        raise HTTPException(status_code=404, detail="해당 titleId를 목록에서 찾을 수 없습니다.")
    return wt


def _trigger_enrich(title_id: str) -> None:
    """구독 직후 백그라운드에서 정보/작가등록을 바로 채운다 (다음 정기 스캔까지 기다리지 않음)."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            await tracker.enrich_one(session, title_id, get_settings())

    asyncio.create_task(_run())


# ── 구독중 / 구독해제 / 제외됨 조회·전환 ──────────────────────────────

@router.get("/webtoons", response_model=list[WebtoonOut])
async def list_webtoons(status: str | None = None):
    if status and status not in (
        repository.STATUS_ACTIVE,
        repository.STATUS_UNSUBSCRIBED,
        repository.STATUS_EXCLUDED,
    ):
        raise HTTPException(status_code=400, detail="status는 active/unsubscribed/excluded 중 하나여야 합니다.")
    rows = (
        await asyncio.to_thread(repository.list_by_status, status)
        if status
        else await asyncio.to_thread(repository.list_all)
    )
    return [_to_out(r) for r in rows]


@router.post("/webtoons/{title_id}/subscribe", response_model=WebtoonOut)
async def subscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/unsubscribe", response_model=WebtoonOut)
async def unsubscribe(title_id: str):
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_UNSUBSCRIBED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/webtoons/{title_id}/acknowledge-finish", response_model=WebtoonOut)
async def acknowledge_finish(title_id: str):
    """알람 제외: 구독은 그대로 유지하고, 완결 알림(구독해제 여부 물어보는 것)만 그만 받는다."""
    await asyncio.to_thread(_get_or_404, title_id)
    await asyncio.to_thread(repository.acknowledge_finish, title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.get("/webtoons/pending-completion", response_model=list[WebtoonOut])
async def list_pending_completion():
    """완결됐는데 아직 구독해제/알람제외 처리를 안 한 구독중인 웹툰 목록 (완결 확인 봇이 폴링)."""
    rows = await asyncio.to_thread(repository.list_by_status, repository.STATUS_ACTIVE)
    pending = [r for r in rows if r.is_finished and not r.finish_ack]
    return [_to_out(r) for r in pending]


# ── 네이버 전체 웹툰 목록 (여기서 바로 구독/목록제외) ──────────────────

class NaverListEntryIn(BaseModel):
    title: str
    thumbnail_url: str = ""

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title이 비어있습니다.")
        return v


@router.get("/naver-list")
async def browse_naver_list():
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)

    existing = await asyncio.to_thread(repository.list_all)
    existing_by_id = {w.title_id: w for w in existing}
    seen_ids: set[str] = set()

    result = []
    for item in items:
        seen_ids.add(item.title_id)
        tracked = existing_by_id.get(item.title_id)
        result.append(
            {
                "title_id": item.title_id,
                "title": item.title_name,
                "thumbnail_url": tracked.thumbnail_url if tracked and tracked.thumbnail_url else item.thumbnail_url,
                "weekdays": item.weekdays,
                "is_finished": item.is_finished,
                "is_paused": item.is_paused,
                "is_adult": item.is_adult,
                "author_summary": item.author_summary,
                "status": tracked.status if tracked else None,
                "genres": tracked.genres if tracked else [],
                "tags": tracked.tags if tracked else [],
                "has_new_episode": item.has_update,
            }
        )

    # 네이버의 요일별 목록 API는 장기 휴재작을 응답에서 아예 빼버린다. 그래서 위 루프만
    # 돌면 이미 추적 중인(구독중/구독해제) 웹툰이 화면에서 통째로 사라질 수 있다 —
    # DB에만 남아있는 건 우리가 갖고 있는 정보로 채워서라도 계속 보이게 한다.
    for wt in existing:
        if wt.title_id in seen_ids or wt.status == repository.STATUS_EXCLUDED:
            continue
        result.append(
            {
                "title_id": wt.title_id,
                "title": wt.title,
                "thumbnail_url": wt.thumbnail_url,
                "weekdays": [],
                "is_finished": wt.is_finished,
                "is_paused": wt.is_paused,
                "is_adult": wt.is_adult,
                "author_summary": "",
                "status": wt.status,
                "genres": wt.genres,
                "tags": wt.tags,
                "has_new_episode": wt.latest_episode_no > wt.last_downloaded_no > 0,
            }
        )

    return result


@router.post("/naver-list/{title_id}/subscribe")
async def naver_list_subscribe(title_id: str, payload: NaverListEntryIn):
    if not await asyncio.to_thread(repository.exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new,
            title_id,
            payload.title,
            False,
            None,
            repository.SOURCE_MANUAL,
            payload.thumbnail_url,
        )
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_ACTIVE)
    _trigger_enrich(title_id)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


@router.post("/naver-list/{title_id}/exclude")
async def naver_list_exclude(title_id: str, payload: NaverListEntryIn):
    if not await asyncio.to_thread(repository.exists, title_id):
        await asyncio.to_thread(
            repository.upsert_new,
            title_id,
            payload.title,
            False,
            None,
            repository.SOURCE_MANUAL,
            payload.thumbnail_url,
        )
    await asyncio.to_thread(repository.set_status, title_id, repository.STATUS_EXCLUDED)
    return _to_out(await asyncio.to_thread(repository.get, title_id))


# ── 작가/태그 자동추가 레지스트리 ──────────────────────────────────

class WatchedAuthorOut(BaseModel):
    author_id: str
    author_name: str
    enabled: bool


class WatchedTagOut(BaseModel):
    tag_id: str
    tag_name: str
    enabled: bool


class WatchedAuthorIn(BaseModel):
    author_id: str
    author_name: str = ""

    @field_validator("author_id")
    @classmethod
    def author_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("author_id가 비어있습니다.")
        return v.strip()


class WatchedTagIn(BaseModel):
    tag_id: str
    tag_name: str = ""

    @field_validator("tag_id")
    @classmethod
    def tag_id_numeric(cls, v: str) -> str:
        if not v.strip().isdigit():
            raise ValueError("tag_id는 숫자만 입력할 수 있습니다.")
        return v.strip()


@router.get("/watched-authors", response_model=list[WatchedAuthorOut])
async def list_watched_authors():
    rows = await asyncio.to_thread(repository.list_watched_authors)
    return [WatchedAuthorOut(author_id=r.author_id, author_name=r.author_name, enabled=r.enabled) for r in rows]


@router.post("/watched-authors", response_model=WatchedAuthorOut)
async def add_watched_author(payload: WatchedAuthorIn):
    await asyncio.to_thread(repository.upsert_watched_author, payload.author_id, payload.author_name, True)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next(r for r in rows if r.author_id == payload.author_id)
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.post("/watched-authors/{author_id}/enable", response_model=WatchedAuthorOut)
async def enable_watched_author(author_id: str):
    await asyncio.to_thread(repository.set_watched_author_enabled, author_id, True)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next((r for r in rows if r.author_id == author_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 작가입니다.")
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.post("/watched-authors/{author_id}/disable", response_model=WatchedAuthorOut)
async def disable_watched_author(author_id: str):
    await asyncio.to_thread(repository.set_watched_author_enabled, author_id, False)
    rows = await asyncio.to_thread(repository.list_watched_authors)
    match = next((r for r in rows if r.author_id == author_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 작가입니다.")
    return WatchedAuthorOut(author_id=match.author_id, author_name=match.author_name, enabled=match.enabled)


@router.get("/watched-tags", response_model=list[WatchedTagOut])
async def list_watched_tags():
    rows = await asyncio.to_thread(repository.list_watched_tags)
    return [WatchedTagOut(tag_id=r.tag_id, tag_name=r.tag_name, enabled=r.enabled) for r in rows]


@router.post("/watched-tags", response_model=WatchedTagOut)
async def add_watched_tag(payload: WatchedTagIn):
    tag_name = payload.tag_name
    if not tag_name:
        settings = get_settings()
        async with aiohttp.ClientSession() as session:
            tag_name = await naver_api.fetch_curation_title_name(
                session, int(payload.tag_id), settings.request_timeout_seconds
            )
    await asyncio.to_thread(repository.upsert_watched_tag, payload.tag_id, tag_name, True)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next(r for r in rows if r.tag_id == payload.tag_id)
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.post("/watched-tags/{tag_id}/enable", response_model=WatchedTagOut)
async def enable_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.set_watched_tag_enabled, tag_id, True)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next((r for r in rows if r.tag_id == tag_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 태그입니다.")
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.post("/watched-tags/{tag_id}/disable", response_model=WatchedTagOut)
async def disable_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.set_watched_tag_enabled, tag_id, False)
    rows = await asyncio.to_thread(repository.list_watched_tags)
    match = next((r for r in rows if r.tag_id == tag_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 태그입니다.")
    return WatchedTagOut(tag_id=match.tag_id, tag_name=match.tag_name, enabled=match.enabled)


@router.delete("/watched-tags/{tag_id}")
async def remove_watched_tag(tag_id: str):
    await asyncio.to_thread(repository.delete_watched_tag, tag_id)
    return {"status": "deleted"}


@router.get("/tags/catalog")
async def get_tag_catalog():
    """네이버가 제공하는 전체 태그 목록 (이름으로 골라서 등록할 때 사용)."""
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        return await naver_api.fetch_tag_catalog(session, settings.request_timeout_seconds)


@router.get("/authors/candidates")
async def list_author_candidates():
    """네이버엔 전체 작가 목록 API가 없어서, 요일별 전체목록의 저자 텍스트에서
    후보 이름들을 뽑아 보여준다 (둘러보기용 — 실제 등록은 이름 검색으로 id를 확정)."""
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    return naver_api.extract_candidate_author_names(items)


@router.get("/authors/search")
async def search_authors(name: str):
    """작가 이름으로 검색 — 네이버 통합검색으로 실제 author_id를 알아낸다."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="검색할 이름을 입력해주세요.")
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        return await tracker.search_authors_by_name(session, name.strip(), settings)


@router.post("/registry/resync")
async def resync_registry():
    """지금 추적 중인 모든 웹툰을 훑어서 작가/태그 레지스트리를 즉시 채운다."""
    async def _run():
        settings = get_settings()
        job_status.start("registry")
        job_status.log_line("registry", "작가/태그 재동기화 시작")
        try:
            async with aiohttp.ClientSession() as session:
                count = await tracker.resync_registry(session, settings)
            job_status.log_line("registry", f"{count}개 웹툰 처리 완료")
            job_status.finish("registry", success=True)
        except Exception as e:
            job_status.log_line("registry", f"오류: {e}")
            job_status.finish("registry", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


# ── 수동 다운로드 ──────────────────────────────────────────────────

class ManualAnalyzeEpisodeOut(BaseModel):
    episode_no: int
    subtitle: str
    owned: bool
    is_locked: bool


class ManualAnalyzeOut(BaseModel):
    title_id: str
    title: str
    episodes: list[ManualAnalyzeEpisodeOut]


class ManualDownloadIn(BaseModel):
    title_id: str
    episode_nos: list[int]

    @field_validator("episode_nos")
    @classmethod
    def not_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("다운로드할 회차를 하나 이상 선택해주세요.")
        return v


@router.get("/manual-download/search")
async def manual_download_search_title(query: str):
    """titleId를 모를 때 제목/작가로 후보를 찾는다 (네이버 통합검색 — 장기휴재작도 나옴)."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="검색할 제목을 입력해주세요.")
    settings = get_settings()
    async with aiohttp.ClientSession() as session:
        results = await naver_api.search_webtoons(session, query.strip(), settings.request_timeout_seconds)
    return [
        {"title_id": item.title_id, "title": item.title_name, "thumbnail_url": item.thumbnail_url}
        for item in results[:10]
    ]


@router.get("/manual-download/analyze", response_model=ManualAnalyzeOut)
async def manual_download_analyze(title_id: str):
    if not title_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="titleId는 숫자만 입력할 수 있습니다.")
    settings = get_settings()
    info, rows = await manual_download.analyze(title_id, settings)
    if info is None:
        raise HTTPException(status_code=400, detail="해당 titleId 정보를 네이버에서 찾지 못했습니다.")
    return ManualAnalyzeOut(
        title_id=title_id,
        title=info.title_name,
        episodes=[
            ManualAnalyzeEpisodeOut(
                episode_no=r.episode_no, subtitle=r.subtitle, owned=r.owned, is_locked=r.is_locked
            )
            for r in rows
        ],
    )


@router.post("/manual-download/run")
async def manual_download_run(payload: ManualDownloadIn):
    settings = get_settings()
    asyncio.create_task(manual_download.download_selected(payload.title_id, payload.episode_nos, settings))
    return {"status": "started"}


# ── 설정 (잡별 스케줄: 끄기 / N분마다 / 특정 요일·시각) ────────────────

class JobScheduleIn(BaseModel):
    mode: str  # off | interval | cron
    interval_minutes: int = 60
    cron_hour: int = 3
    cron_minute: int = 0
    cron_days: list[str] = []

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        if v not in schedule_config.VALID_MODES:
            raise ValueError(f"mode는 {schedule_config.VALID_MODES} 중 하나여야 합니다.")
        return v

    @field_validator("interval_minutes")
    @classmethod
    def interval_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("주기는 1분 이상이어야 합니다.")
        return v

    @field_validator("cron_hour")
    @classmethod
    def hour_in_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("시(hour)는 0~23이어야 합니다.")
        return v

    @field_validator("cron_minute")
    @classmethod
    def minute_in_range(cls, v: int) -> int:
        if not (0 <= v <= 59):
            raise ValueError("분(minute)은 0~59여야 합니다.")
        return v

    @field_validator("cron_days")
    @classmethod
    def days_must_be_valid(cls, v: list[str]) -> list[str]:
        invalid = [d for d in v if d not in schedule_config.VALID_DAYS]
        if invalid:
            raise ValueError(f"알 수 없는 요일: {invalid}")
        return v


class SchedulesIn(BaseModel):
    discovery_job: JobScheduleIn
    download_job: JobScheduleIn


def _schedule_to_dict(job_id: str) -> dict:
    s = schedule_config.get_schedule(job_id, scheduler_mod.DEFAULT_SCHEDULES[job_id])
    return {
        "mode": s.mode,
        "interval_minutes": s.interval_minutes,
        "cron_hour": s.cron_hour,
        "cron_minute": s.cron_minute,
        "cron_days": s.cron_days,
    }


@router.get("/settings")
async def get_schedules():
    return await asyncio.to_thread(
        lambda: {job_id: _schedule_to_dict(job_id) for job_id in scheduler_mod.DEFAULT_SCHEDULES}
    )


@router.post("/settings")
async def update_schedules(payload: SchedulesIn, request: Request):
    for job_id, job_in in payload.model_dump().items():
        job_schedule = schedule_config.JobSchedule(**job_in)
        await asyncio.to_thread(schedule_config.set_schedule, job_id, job_schedule)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await asyncio.to_thread(scheduler_mod.reschedule_all, scheduler)

    return await get_schedules()


# ── 디스코드 설정 + 테스트 ─────────────────────────────────────────

class DiscordSettingsOut(BaseModel):
    webhook_url: str
    bot_token_set: bool
    notify_channel_id: str
    bot_ready: bool


class DiscordSettingsIn(BaseModel):
    webhook_url: str = ""
    bot_token: str = ""  # 비워두면 기존 값 유지
    notify_channel_id: str = ""


@router.get("/settings/discord", response_model=DiscordSettingsOut)
async def get_discord_settings():
    return DiscordSettingsOut(
        webhook_url=await asyncio.to_thread(discord_config.get_webhook_url),
        bot_token_set=bool(await asyncio.to_thread(discord_config.get_bot_token)),
        notify_channel_id=await asyncio.to_thread(discord_config.get_notify_channel_id),
        bot_ready=discord_bot.is_ready(),
    )


@router.post("/settings/discord", response_model=DiscordSettingsOut)
async def update_discord_settings(payload: DiscordSettingsIn):
    await asyncio.to_thread(discord_config.set_webhook_url, payload.webhook_url)
    await asyncio.to_thread(discord_config.set_bot_token, payload.bot_token)
    await asyncio.to_thread(discord_config.set_notify_channel_id, payload.notify_channel_id)
    await discord_bot.restart_bot()
    return await get_discord_settings()


@router.post("/settings/discord/test-webhook")
async def test_discord_webhook():
    success, message = await discord_notify.send_test_webhook_message()
    return {"success": success, "message": message}


@router.post("/settings/discord/test-bot")
async def test_discord_bot():
    success, message = await discord_bot.send_test_message()
    return {"success": success, "message": message}


# ── 백업 / 복원 ────────────────────────────────────────────────────

@router.get("/backup")
async def download_backup():
    return await asyncio.to_thread(repository.export_all)


@router.post("/restore")
async def restore_backup(data: dict):
    await asyncio.to_thread(repository.restore_all, data)
    return {"status": "restored"}


# ── 수동 실행 + 진행상황 ──────────────────────────────────────────

@router.get("/jobs/status")
async def jobs_status():
    return await asyncio.to_thread(job_status.snapshot)


@router.get("/jobs/history")
async def jobs_history(limit_per_job: int = 10):
    """스케줄대로 자동 실행된 잡이 실제로 돌았는지/성공했는지 나중에 확인할 수 있는 이력."""
    return await asyncio.to_thread(repository.list_job_history, limit_per_job)


@router.post("/jobs/discovery/run")
async def trigger_discovery_job():
    asyncio.create_task(scheduler_mod.run_discovery_job())
    return {"status": "started"}


@router.post("/jobs/download/run")
async def trigger_download_job():
    asyncio.create_task(scheduler_mod.run_download_job())
    return {"status": "started"}
