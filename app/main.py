"""
앱 진입점. `uvicorn app.main:app`으로 실행한다.

보안: 디스코드 봇 토큰/웹훅 URL/쿠키 값이 로그에 원문으로 찍히지 않도록
RedactingFilter를 전역 로깅에 건다 (요구사항: 민감 정보 마스킹).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import get_settings
from app.scheduler import create_scheduler


class RedactingFilter(logging.Filter):
    """설정된 민감 값들을 로그 메시지에서 마스킹한다."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        for secret in self._secrets:
            if secret in message:
                message = message.replace(secret, "***REDACTED***")
        record.msg = message
        record.args = ()
        return True


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    redacting_filter = RedactingFilter([settings.webtoon_bot_token, settings.webtoon_webhook_url])
    logging.getLogger().addFilter(redacting_filter)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="웹툰 구독 관리", lifespan=lifespan)
app.include_router(api_router)
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
