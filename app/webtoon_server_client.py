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
