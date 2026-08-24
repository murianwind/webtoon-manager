"""
환경변수 기반 설정.

하드코딩 금지 원칙: API 엔드포인트/타임아웃/배치 크기 등 조정 가능한 값은
전부 여기서만 정의하고, 나머지 모듈은 이 Settings 객체를 통해서만 값을 읽는다.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 경로
    download_root: str = "/data/Webtoon_Download"
    database_path: str = "/data/webtoons.db"
    cookie_file_path: str = "/data/cookies/chokobo_murian.json"

    # 디스코드
    webtoon_webhook_url: str = ""
    webtoon_bot_token: str = ""
    webtoon_notify_channel_id: str = ""

    # 태그 자동추가 (콤마 구분 문자열로 받아서 파싱)
    webtoon_tag_ids: str = "134,133"

    # 다운로드 동작
    folder_zero_fill: int = 4
    image_zero_fill: int = 4
    batch_size: int = 5
    max_concurrent_downloads: int = 10
    delay_seconds: float = 1.0
    request_timeout_seconds: int = 10

    # 스케줄 주기 (분)
    scan_interval_minutes: int = 360
    download_interval_minutes: int = 60
    commands_only_interval_minutes: int = 5

    # 웹서버
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def tag_ids(self) -> list[int]:
        return [int(x) for x in self.webtoon_tag_ids.split(",") if x.strip().isdigit()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
