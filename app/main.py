"""
앱 진입점. `uvicorn app.main:app`으로 실행한다.

보안: 디스코드 봇 토큰/웹훅 URL/쿠키 값이 로그에 원문으로 찍히지 않도록
RedactingFilter를 전역 로깅에 건다 (요구사항: 민감 정보 마스킹).
토큰이 이제 DB에서도 바뀔 수 있어서(설정 페이지), 시작 시점 값을 고정해두지 않고
매 로그 라인마다 discord_config에서 현재 값을 다시 조회해서 마스킹한다.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app import discord_config
from app.api.routes import router as api_router
from app.discord_bot import start_bot, stop_bot
from app.scheduler import create_scheduler
from app.tracker import ensure_default_tags_seeded


class RedactingFilter(logging.Filter):
    """디스코드 웹훅 URL/봇 토큰을 로그 메시지에서 마스킹한다 (매 호출마다 현재 값 조회)."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = [discord_config.get_bot_token(), discord_config.get_webhook_url()]
        secrets = [s for s in secrets if s]
        if not secrets:
            return True
        message = record.getMessage()
        for secret in secrets:
            if secret in message:
                message = message.replace(secret, "***REDACTED***")
        record.msg = message
        record.args = ()
        return True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger().addFilter(RedactingFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    ensure_default_tags_seeded()
    await start_bot()
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)
    await stop_bot()


app = FastAPI(title="웹툰 구독 관리", lifespan=lifespan)
app.include_router(api_router)
class NoCacheStaticFiles(StaticFiles):
    """
    app.js/style.css/index.html에 파일명 해시가 없어서, 브라우저가 자체 판단으로
    캐싱해버리면 서버는 최신 버전인데 화면은 예전 버전을 계속 보여주는 문제가
    실제로 있었다(고쳤다고 안내드려도 브라우저 캐시 때문에 반영이 안 보임) —
    매번 서버에 검증(If-None-Match)하도록 강제해서, 실제로 안 바뀌었으면
    304로 가볍게 넘어가고 바뀌었으면 항상 새로 받도록 한다.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", NoCacheStaticFiles(directory="app/static", html=True), name="static")
