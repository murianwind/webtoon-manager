"""환경변수로 바꿀 수 있는 옵션은 문서에 있는 6개(경로)뿐이다 — 숨겨진 옵션이 없어야 한다.

- 속도/동시 요청/타임아웃/번호 자릿수 같은 값은 환경변수로 바꿀 수 없는 고정값이다(속도 조절은 설정 화면에서 바꾼다).
- 예전에 환경변수로 이 값들을 지정해 둔 배포는 값이 조용히 무시되지 않도록 시작할 때 경고를 남긴다.
- README와 docker-compose.yml은 남은 모든 옵션을 빠짐없이 적는다(이 테스트가 새 옵션이 문서에서 빠지는 것을 막는다).
"""
import inspect
import logging
import os
import re
import tempfile
from pathlib import Path

import yaml

from app import config, db
from app.config import Settings
db.get_connection()

ROOT = Path(__file__).resolve().parents[2]
PATH_OPTIONS = {"download_root", "webtoon_download_host_path", "archive_root", "rclone_config_path", "database_path", "cookie_file_path"}
FIXED = {"folder_zero_fill": 4, "image_zero_fill": 4, "max_concurrent_downloads": 10, "artist_scan_concurrency": 5,
         "max_new_episodes_per_title": 10, "batch_rest_minutes": 5.0, "delay_seconds": 1.0, "request_timeout_seconds": 10}
README = (ROOT / "README.md").read_text(encoding="utf-8")
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

# Scenario 1: 환경변수로 덮어쓸 수 있는 필드는 경로 6개뿐이다
assert set(Settings.model_fields) == PATH_OPTIONS, set(Settings.model_fields) ^ PATH_OPTIONS

# Scenario 2: 고정값은 환경변수를 줘도 바뀌지 않는다(값이 조용히 적용되는 숨겨진 옵션이 없다)
saved = {name.upper(): os.environ.get(name.upper()) for name in FIXED}
try:
    for name in FIXED: os.environ[name.upper()] = "999"
    fresh = Settings()
    assert {name: getattr(fresh, name) for name in FIXED} == FIXED, {name: getattr(fresh, name) for name in FIXED}
finally:
    for key, value in saved.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value
print("1~2) 환경변수 옵션은 경로 6개뿐 OK")

# Scenario 3: 예전 환경변수가 남아 있는지 찾는다(환경변수와 .env 파일, 대소문자 무관). 현재 옵션은 포함하지 않는다
assert config.REMOVED_ENV_VARIABLES == tuple(sorted(n.upper() for n in FIXED)) or set(config.REMOVED_ENV_VARIABLES) == {n.upper() for n in FIXED}
assert config.removed_env_variables({"BATCH_REST_MINUTES": "1", "download_root": "/x", "delay_seconds": "3"}, env_file=None) == ["BATCH_REST_MINUTES", "DELAY_SECONDS"]
assert config.removed_env_variables({}, env_file=None) == []
with tempfile.TemporaryDirectory() as tmp:
    dotenv = Path(tmp) / ".env"; dotenv.write_text("MAX_CONCURRENT_DOWNLOADS=3\nDOWNLOAD_ROOT=/x\n# IMAGE_ZERO_FILL=5\n", encoding="utf-8")
    assert config.removed_env_variables({}, env_file=dotenv) == ["MAX_CONCURRENT_DOWNLOADS"]
    assert config.removed_env_variables({}, env_file=Path(tmp) / "없는파일") == []
# Scenario 4: 시작할 때 경고 한 번 — 이름과 "설정 화면"/"무시" 안내가 들어 있고, 해당 변수가 없으면 조용하다
records = []
class Capture(logging.Handler):
    def emit(self, record): records.append(record)
logger = logging.getLogger("test.config"); logger.addHandler(Capture()); logger.setLevel(logging.INFO)
config.warn_removed_env_variables(logger, {"BATCH_REST_MINUTES": "1"}, env_file=None)
assert len(records) == 1 and records[0].levelno == logging.WARNING and "BATCH_REST_MINUTES" in records[0].getMessage() and "무시" in records[0].getMessage() and "설정 화면" in records[0].getMessage()
records.clear(); config.warn_removed_env_variables(logger, {"DOWNLOAD_ROOT": "/x"}, env_file=None); assert records == []
import app.main as main
assert "warn_removed_env_variables" in inspect.getsource(main.lifespan), "앱이 시작할 때 이 경고를 호출한다"
print("3~4) 예전 환경변수 경고 OK")

# Scenario 5: README와 docker-compose.yml에 남은 옵션이 빠짐없이 있다
compose = yaml.safe_load(COMPOSE_TEXT)
environment = compose["services"]["webtoon-manager"]["environment"]
for name in sorted(PATH_OPTIONS):
    assert name.upper() in environment, f"docker-compose.yml의 environment에 {name.upper()}가 없다"
    assert name.upper() in README, f"README에 {name.upper()}가 없다"
for variable in sorted(set(re.findall(r"\$\{([A-Z_]+)", COMPOSE_TEXT))):
    assert variable in README, f"docker-compose.yml이 쓰는 ${{{variable}}}가 README에 없다"
    assert variable in COMPOSE_TEXT.split("services:")[0], f"docker-compose.yml 머리말의 변수 목록에 {variable}가 없다"
assert "모든 설정 옵션" in README
for removed in config.REMOVED_ENV_VARIABLES:
    assert removed in README, f"README의 '더 이상 쓰이지 않는 환경변수' 안내에 {removed}가 없다"
    assert removed not in environment, f"docker-compose.yml의 environment에 지운 옵션 {removed}가 남아 있다"
print("5) README/docker-compose 옵션 빠짐없음 OK")
print("ALL OK")
