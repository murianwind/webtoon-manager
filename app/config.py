"""
앱 설정.

**환경변수(또는 작업 폴더의 .env)로 바꿀 수 있는 것은 경로 6개뿐이다**(DOWNLOAD_ROOT, WEBTOON_DOWNLOAD_HOST_PATH, ARCHIVE_ROOT, RCLONE_CONFIG_PATH,
DATABASE_PATH, COOKIE_FILE_PATH) — README와 docker-compose.yml에 전부 적혀 있고, tests/backend/test_config_options.py가 이 목록과 문서가 어긋나지 않게 검사한다.
숨겨진 옵션을 두지 않으려고, 나머지 값(요청 간격, 타임아웃, 동시 요청 수, 번호 자릿수, 속도 조절의 기본값)은 환경변수로 읽지 않는 **고정값**이다.
다운로드 속도(작품당 한 번에 받을 회차, 쉬는 시간)는 설정 화면에서 바꾸고(app/download_pacing.py) 여기 값은 그 기본값이다.

디스코드 설정(웹훅/봇토큰/채널ID), 태그 자동추가 목록, 잡 실행 스케줄도 여기(env)에서 관리하지 않는다 — 전부 웹 설정 페이지에서 입력받아 DB(settings/
watched_tags 테이블)에 저장한다. 이 파일에 남기면 아무도 안 읽는 죽은 값이 되므로 필드 자체를 두지 않는다.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── 환경변수로 바꿀 수 있는 옵션(경로) ──
    download_root: str = "/webtoon_download"
    # 선택: compose의 WEBTOON_DOWNLOAD_HOST_PATH(호스트의 실제 폴더)를 컨테이너 환경변수로 넘겨주면, 설정 화면이 "받는 폴더"를
    # 컨테이너 경로뿐 아니라 호스트 경로로도 보여준다. 안 넘기면 컨테이너 경로만 보인다(컨테이너는 호스트 경로를 스스로 알 수 없다).
    webtoon_download_host_path: str = ""
    archive_root: str = ""
    rclone_config_path: str = ""
    database_path: str = "/data/webtoons.db"
    cookie_file_path: str = "/cookies/chokobo_murian.json"

    # ── 고정값(환경변수로 바꿀 수 없다 — ClassVar라서 pydantic 필드가 아니고 환경변수를 읽지 않는다) ──
    folder_zero_fill: ClassVar[int] = 4  # 회차 폴더/파일 번호 자릿수(0012_…). 바꾸면 이미 받은 파일과 이름 규칙이 어긋난다
    image_zero_fill: ClassVar[int] = 4  # 이미지 파일 번호 자릿수
    max_concurrent_downloads: ClassVar[int] = 10  # 한 회차의 이미지를 동시에 받는 수
    artist_scan_concurrency: ClassVar[int] = 5  # 작가/태그 스캔과 미등록 새 에피소드 조회 때 네이버 API 동시 요청 수 제한
    max_new_episodes_per_title: ClassVar[int] = 10  # 속도 조절의 기본값: 작품당 한 번에 받을 회차 상한(0 이하=무제한). 설정 화면에서 바꾼 값이 우선
    batch_rest_minutes: ClassVar[float] = 5.0  # 속도 조절의 기본값: 상한에 걸렸을 때 다음 배치 전에 쉬는 시간(분). 설정 화면에서 바꾼 값이 우선
    delay_seconds: ClassVar[float] = 1.0  # 회차/작품 사이 간격(초)
    request_timeout_seconds: ClassVar[int] = 10  # 요청 하나의 제한 시간(초)


# 예전에는 환경변수로 바꿀 수 있었지만 지금은 무시되는 옵션 — 이 값을 지정해 둔 배포에는 시작할 때 경고를 남긴다.
REMOVED_ENV_VARIABLES = (
    "MAX_NEW_EPISODES_PER_TITLE",
    "BATCH_REST_MINUTES",
    "DELAY_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "MAX_CONCURRENT_DOWNLOADS",
    "ARTIST_SCAN_CONCURRENCY",
    "FOLDER_ZERO_FILL",
    "IMAGE_ZERO_FILL",
)


def _names_in_env_file(env_file: Path) -> set[str]:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    names = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.add(line.split("=", 1)[0].removeprefix("export ").strip().upper())
    return names


def removed_env_variables(environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> list[str]:
    """환경변수와 .env 파일에서 지금은 무시되는 예전 옵션(REMOVED_ENV_VARIABLES)이 지정돼 있는 것(대소문자 무관)을 목록 순서대로 돌려준다."""
    present = {key.upper() for key in (os.environ if environ is None else environ)}
    if env_file is not None:
        present |= _names_in_env_file(env_file)
    return [name for name in REMOVED_ENV_VARIABLES if name in present]


def warn_removed_env_variables(logger: logging.Logger, environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> None:
    """예전 환경변수가 남아 있으면 값이 조용히 무시되지 않도록 시작할 때 경고를 한 번 남긴다."""
    names = removed_env_variables(environ, env_file)
    if names:
        logger.warning(
            "더 이상 쓰이지 않는 환경변수가 지정돼 있어 무시합니다: %s — 다운로드 속도(작품당 한 번에 받을 회차, 쉬는 시간)는 설정 화면의 "
            "\"다운로드 속도 조절\"에서 바꾸고, 나머지는 고정값입니다. 환경변수/.env에서 지우세요.",
            ", ".join(names),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
