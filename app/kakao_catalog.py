"""
"웹툰 전체목록"/"제외됨"/"구독해제" 탭에 쓰는 카카오페이지 요일별 목록 캐시.

카카오페이지 목록은 요일마다 여러 페이지를 받아야 해서(전체 약 60번 요청, 처음엔 10초 안팎) 화면을 열 때마다
받으면 브라우저도 서버도 느려진다. 그래서:

- 화면(API)은 **캐시에서만** 읽는다 — 네트워크를 기다리지 않는다.
- 목록은 **백그라운드로** 채운다: 프로그램이 시작될 때(업데이트 직후에도 항상 새로 채움), 그리고 3시간마다,
  그리고 "새로고침" 버튼을 눌렀을 때. 화면이 캐시가 오래된 걸 발견하면(스케줄이 못 돌았을 때) 그때도 백그라운드로 채운다.
- 캐시는 DB에도 저장해서 재시작 직후에도 바로 보여주고(새 목록이 채워질 때까지), 저장 형식이 바뀐 버전의 캐시는 버린다.
- 일부 요일 조회가 실패한 불완전한 결과로 멀쩡한 예전 캐시를 덮어쓰지 않는다(캐시가 아예 없을 때만 임시로 씀).
- 예전 카카오웹툰 시절의 기록(전체목록/구독해제/제외됨의 옛 주소)은 목록을 처음 온전히 받은 직후 딱 한 번
  카카오페이지 작품으로 자동 변환한다(migrate_legacy_once).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from app import kakao_api, repository
from app.config import get_settings
from app.file_utils import title_key

log = logging.getLogger(__name__)

SETTING_KEY = "kakao_catalog_snapshot"
LEGACY_DONE_KEY = "kakao_legacy_migrated"
FORMAT_VERSION = 1
REFRESH_INTERVAL_MINUTES = 180
_STALE_AFTER_SECONDS = REFRESH_INTERVAL_MINUTES * 60 + 300  # 스케줄이 한 번 못 돌았을 때를 위한 여유

_state: dict = {"loaded": False, "items": None, "fetched_at": None}  # fetched_at: 에폭 초(불완전하게 채운 임시 캐시는 0)
_refresh_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()  # 진행 중인 백그라운드 작업이 중간에 정리되지 않게 참조를 잡아둔다


def _load_persisted() -> None:
    if _state["loaded"]:
        return
    _state["loaded"] = True
    raw = repository.get_setting(SETTING_KEY)
    if not raw:
        return
    try:
        data = json.loads(raw)
        if data.get("v") == FORMAT_VERSION and isinstance(data.get("items"), list):
            _state["items"], _state["fetched_at"] = data["items"], data.get("fetched_at")
    except Exception as e:
        log.warning("저장된 카카오 목록 캐시를 읽지 못해 버립니다: %s", e)


def snapshot() -> tuple[list[dict], float | None]:
    """(목록, 받은 시각(에폭 초)) — 캐시에 있는 그대로. 네트워크는 부르지 않는다. 캐시가 없으면 ([], None)."""
    _load_persisted()
    return list(_state["items"] or []), _state["fetched_at"]


def is_refreshing() -> bool:
    """갱신 중이거나, 갱신 작업이 만들어졌지만 아직 시작 전인 상태(만든 직후 응답에서도 "갱신 중"으로 보이게)."""
    return _refresh_lock.locked() or bool(_tasks)


def _persist(items: list[dict], fetched_at: float) -> None:
    repository.set_setting(SETTING_KEY, json.dumps({"v": FORMAT_VERSION, "fetched_at": fetched_at, "items": items}, ensure_ascii=False))


async def refresh() -> bool:
    """새로 받아서 캐시를 갱신한다(이미 갱신 중이면 아무것도 안 하고 False). 갱신이 됐으면 True."""
    if _refresh_lock.locked():
        return False
    async with _refresh_lock:
        _load_persisted()
        try:
            async with aiohttp.ClientSession() as session:
                items, all_ok = await kakao_api.fetch_weekday_catalog_checked(session, get_settings().request_timeout_seconds)
        except Exception as e:
            log.warning("카카오 목록 새로고침 예외 — 예전 캐시를 그대로 둡니다: %s", e)
            return False
        if items and all_ok:
            fetched_at = time.time()
        elif items and _state["items"] is None:
            fetched_at = 0.0  # 캐시가 아예 없을 때만 불완전한 결과를 임시로 쓰고, 곧 다시 채우게 오래된 것으로 표시
        else:
            log.warning("카카오 목록 새로고침이 온전하지 않아 예전 캐시를 그대로 둡니다")
            return False
        _state["items"], _state["fetched_at"] = items, fetched_at
        try:
            _persist(items, fetched_at)
        except Exception as e:
            log.warning("카카오 목록 캐시 저장 실패(메모리에는 반영됨): %s", e)
        if all_ok:  # 온전한 목록일 때만 옛 기록을 변환한다(일부만 받은 목록으로 "못 찾음"을 잘못 판단하지 않도록)
            try:
                await migrate_legacy_once(items)
            except Exception as e:
                log.warning("예전 카카오 기록 자동 변환 중 예외 — 다음 갱신 때 다시 시도합니다: %s", e)
        return True


async def migrate_legacy_once(items: list[dict], *, force: bool = False) -> dict | None:
    """예전 카카오웹툰 기록을 카카오페이지 작품으로 옮긴다(제목이 정확히 하나만 일치할 때). 지금 연재 중인 작품은 받은 목록에서,
    완결/휴재 작품은 카카오페이지 검색으로 찾는다. 못 옮긴 "제외됨"은 지우고(연재 중이면 전체목록에 다시 나타난다), 구독해제/구독
    이력이 있는 기록은 남긴다. 한 번 끝나면 다시 하지 않는다(force=True는 테스트/재시도용). 이미 끝났으면 None."""
    if not force and repository.get_setting(LEGACY_DONE_KEY) == "1":
        return None
    legacy_titles = await asyncio.to_thread(repository.list_legacy_kakao_titles)
    result: dict = {"migrated": 0, "unmatched": [], "deleted": 0}
    if legacy_titles:
        in_catalog = {title_key(item["title_name"]) for item in items}
        not_in_catalog = sorted(t for t in legacy_titles if title_key(t) not in in_catalog)
        async with aiohttp.ClientSession() as session:
            searched, candidates = await kakao_api.search_series_by_titles(session, not_in_catalog, get_settings().request_timeout_seconds)
        known_ids = {item["title_id"] for item in items}
        all_items = items + [item for item in searched if item["title_id"] not in known_ids]
        result = await asyncio.to_thread(repository.migrate_legacy_kakao_webtoons, all_items, candidates)
        result["deleted"] = await asyncio.to_thread(repository.delete_unmatched_legacy_excluded)
        result["unmatched"] = [u for u in result["unmatched"] if u["status"] != repository.STATUS_EXCLUDED]
        log.info("예전 카카오 기록 자동 변환: %s개 옮김, 못 옮긴 제외됨 %s개 삭제, 이력 있는 미변환 %s개 남김", result["migrated"], result["deleted"], len(result["unmatched"]))
    await asyncio.to_thread(repository.set_setting, LEGACY_DONE_KEY, "1")
    return result


def start_refresh() -> bool:
    """백그라운드로 새로 받기 시작한다(기다리지 않음). 이미 갱신 중이거나 시작 대기 중이면 False."""
    if is_refreshing():
        return False
    task = asyncio.create_task(refresh())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def ensure_fresh() -> bool:
    """캐시가 없거나 너무 오래됐으면 백그라운드로 채운다(스케줄이 못 돌았을 때의 안전장치). 시작했으면 True."""
    items, fetched_at = snapshot()
    if not items or fetched_at is None or time.time() - fetched_at > _STALE_AFTER_SECONDS:
        return start_refresh()
    return False
