"""
환경변수 기반 설정.

하드코딩 금지 원칙: API 타임아웃 등 조정 가능한 값은 전부 여기서만 정의하고,
나머지 모듈은 이 Settings 객체를 통해서만 값을 읽는다.

디스코드 설정(웹훅/봇토큰/채널ID), 태그 자동추가 목록, 잡 실행 스케줄은 더 이상
여기(env)에서 관리하지 않는다 — 전부 웹 설정 페이지에서 입력받아 DB(settings/
watched_tags 테이블)에 저장한다. 이 파일에 남기면 아무도 안 읽는 죽은 값이 되므로
필드 자체를 두지 않는다.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 경로
    download_root: str = "/data/Webtoon_Download"
    database_path: str = "/data/webtoons.db"
    cookie_file_path: str = "/data/cookies/chokobo_murian.json"

    # 다운로드 동작
    folder_zero_fill: int = 4
    image_zero_fill: int = 4
    max_concurrent_downloads: int = 10
    artist_scan_concurrency: int = 5  # 작가/태그 스캔 시 네이버 API 동시 요청 수 제한
    delay_seconds: float = 1.0
    request_timeout_seconds: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
