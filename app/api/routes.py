"""API 라우터 — 도메인별 모듈(app/api/*.py)을 /api 아래 하나로 묶는다. 실제 엔드포인트는 각 모듈에 있다."""

from fastapi import APIRouter

from app.api import (
    app_settings,
    archive,
    discord,
    history,
    jobs,
    kakao_download,
    kakao_webtoons,
    manual_download,
    registry,
    schedule,
    system,
    webtoons,
)

router = APIRouter(prefix="/api")
# 모듈 순서는 원래 파일에서 각 도메인의 첫 엔드포인트가 나오던 순서 — 경로가 겹칠 수 있는 라우트의 우선순위를 그대로 유지한다
for _module in (
    webtoons,
    kakao_webtoons,
    kakao_download,
    app_settings,
    registry,
    jobs,
    manual_download,
    schedule,
    discord,
    system,
    history,
    archive,
):
    router.include_router(_module.router)
