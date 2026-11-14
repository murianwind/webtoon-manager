"""
별도 프로젝트인 webtoon-server(뷰어)의 /api/lookup/latest API를 호출해서, 방금 받은
작품의 "바로가기" 링크를 얻는다. 계약은 예전 hermes webtoon_checker.py가 쓰던 것과
동일하다고 가정한다: GET {base_url}/api/lookup/latest?series={작품명} -> {"url": "..."}

webtoon-server 자체는 이 프로젝트 소스에 없으므로, 실제 스펙이 다르면 이 파일만
고치면 된다 — 호출부(scheduler.py)는 이 함수의 반환값(성공 시 URL, 실패 시 None)만 본다.
"""

import logging

import aiohttp

log = logging.getLogger(__name__)


async def fetch_reader_url(
    session: aiohttp.ClientSession, base_url: str, series_name: str, timeout_seconds: int
) -> str | None:
    """조회에 실패해도(404, 타임아웃, 형식 오류 등) 예외를 던지지 않고 None을 반환한다 —
    이 링크는 리포트 메시지를 보강하는 부가 정보일 뿐이라, 실패해도 리포트 자체가
    중단되면 안 된다."""
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/api/lookup/latest"
    try:
        async with session.get(
            url,
            params={"series": series_name},
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()
            return data.get("url") or None
    except Exception as e:
        log.warning("webtoon-server 바로가기 조회 실패 (series=%s): %s", series_name, e)
        return None


async def fetch_read_numbers(
    session: aiohttp.ClientSession, base_url: str, series_name: str, timeout_seconds: int
) -> frozenset[int] | None:
    """뷰어에서 이 작품의 '읽은' 회차 번호들을 얻는다: GET {base_url}/api/lookup/read-state?series={작품명}
    -> {"series_id": "...", "read_numbers": [1, 2, ...]} ('읽는 중'인 회차는 뷰어가 이미 뺀다).
    실패(미설정, 404, 타임아웃, 형식 오류)는 모두 None — 호출부가 "알 수 없음"으로 보고 거르지 않는다.
    읽은 회차가 하나도 없는 것(빈 집합)과 구분된다."""
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/api/lookup/read-state"
    try:
        async with session.get(
            url,
            params={"series": series_name},
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()
            return frozenset(int(n) for n in data["read_numbers"])
    except Exception as e:
        log.warning("webtoon-server 읽음 상태 조회 실패 (series=%s): %s", series_name, e)
        return None


_SCAN_HEADER = "X-Integration-Token"


async def start_scan(session: aiohttp.ClientSession, base_url: str, token: str, timeout_seconds: int, scope: str) -> tuple[str, bool]:
    """뷰어에 스캔을 시킨다: POST {base_url}/api/integration/rescan 본문 {"scope": scope} (헤더 X-Integration-Token). 스캔이 오래 걸려도 응답은 바로 온다.
    scope: "fast"(로컬 플랫폼만) / "slow"(네트워크 드라이브만) / "all"(둘 다). 돌려주는 값: (결과, 뷰어가 범위를 아는지)
    결과 "started"(시작함) / "running"(이미 스캔 중) / "unsupported"(연동이 없거나 토큰이 안 맞음: 404/401/403) / "error"(연결 오류 등).
    옛 뷰어는 본문을 무시하고 전체를 스캔하며 응답에 "scope"가 없다 — 그러면 두 번째 값이 False(호출부가 전체 스캔 하나로 다룬다)."""
    try:
        async with session.post(
            base_url.rstrip("/") + "/api/integration/rescan", headers={_SCAN_HEADER: token}, json={"scope": scope},
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status in (401, 403, 404):
                return "unsupported", False
            if response.status != 200:
                log.warning("뷰어 스캔 시작 실패: HTTP %s", response.status)
                return "error", False
            data = await response.json(content_type=None)
            return ("started" if data.get("started") else "running"), "scope" in data
    except Exception as e:
        log.warning("뷰어 스캔 시작 중 오류: %s", e)
        return "error", False


async def scan_status(session: aiohttp.ClientSession, base_url: str, token: str, timeout_seconds: int, scope: str) -> dict | None:
    """뷰어의 스캔 진행 상태: GET {base_url}/api/integration/scan-status?scope= → {"running": bool, ...}(그 범위 기준; 옛 뷰어는 범위를 무시하고 전체 기준). 못 읽으면 None(다음에 다시 확인)."""
    try:
        async with session.get(
            base_url.rstrip("/") + "/api/integration/scan-status", headers={_SCAN_HEADER: token}, params={"scope": scope},
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
            return data if isinstance(data, dict) and "running" in data else None
    except Exception as e:
        log.warning("뷰어 스캔 상태 확인 중 오류: %s", e)
        return None
