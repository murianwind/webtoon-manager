"""다운로드 뒤 웹툰 뷰어 서버(webtoon-server)에 스캔을 시킨다 — 리포트의 "바로가기" 링크가 방금 받은 회차를 반영하도록.

뷰어는 네트워크 드라이브라 스캔이 15~30분 걸릴 수 있어서 요청을 오래 붙잡지 않는다: 시작 요청(POST /api/integration/rescan)은 바로 돌아오고, 이후
1분마다 진행 상태(GET /api/integration/scan-status)만 확인한다. 최대 대기 시간(기본 45분)이 지나거나 뷰어가 이 연동을 지원하지 않으면(404/401/403) 기다리지
않고 리포트를 보낸다. 뷰어는 로그인(PROFILES_ENABLED)이 켜져 있어도 쓸 수 있게 별도 토큰(뷰어의 INTEGRATION_TOKEN)으로 인증한다 — 토큰은 암호화해서 저장한다.
"""
import logging
from datetime import datetime, timedelta, timezone

from app import job_status, repository, webtoon_server_client
from app.crypto import decrypt, encrypt

log = logging.getLogger(__name__)

ENABLED_KEY = "viewer_scan_enabled"
TOKEN_KEY = "viewer_scan_token"
MAX_WAIT_KEY = "viewer_scan_max_wait_minutes"
DEFAULT_MAX_WAIT_MINUTES = 45
MAX_WAIT_LIMIT_MINUTES = 240
_SERVER_URL_KEY = "webtoon_server_url"


def is_enabled() -> bool:
    return repository.get_setting(ENABLED_KEY) == "1"


def get_token() -> str:
    return decrypt(repository.get_setting(TOKEN_KEY) or "")


def get_max_wait_minutes() -> int:
    raw = repository.get_setting(MAX_WAIT_KEY)
    try:
        return min(MAX_WAIT_LIMIT_MINUTES, max(1, int(raw))) if raw is not None else DEFAULT_MAX_WAIT_MINUTES
    except ValueError:
        return DEFAULT_MAX_WAIT_MINUTES


def set_config(*, enabled: bool, token: str | None = None, max_wait_minutes: int | None = None) -> None:
    """token: None이면 기존 값 유지, 빈 문자열이면 지운다."""
    if token and not token.isascii():
        raise ValueError("연동 토큰은 영문/숫자/기호만 쓸 수 있습니다(HTTP 헤더 값으로 보내기 때문입니다).")
    if max_wait_minutes is not None and not (1 <= max_wait_minutes <= MAX_WAIT_LIMIT_MINUTES):
        raise ValueError(f"스캔 대기 시간은 1~{MAX_WAIT_LIMIT_MINUTES}분이어야 합니다.")
    repository.set_setting(ENABLED_KEY, "1" if enabled else None)
    if token is not None:
        repository.set_setting(TOKEN_KEY, encrypt(token) if token else None)
    if max_wait_minutes is not None:
        repository.set_setting(MAX_WAIT_KEY, str(max_wait_minutes))


def is_ready() -> bool:
    """스캔을 시킬 수 있다: 켜져 있고 뷰어 서버 주소와 토큰이 있다."""
    return is_enabled() and bool(repository.get_setting(_SERVER_URL_KEY)) and bool(get_token())


async def advance(session, state: dict, now: datetime, timeout_seconds: int) -> dict:
    """이 묶음의 스캔을 한 걸음 진행한다(1분마다 불림): 시작 전이면 시작 요청만 보내고, 시작했으면 끝났는지만 확인한다.
    끝났거나(또는 포기해서) 리포트를 보내도 되면 scan_done=True. 바꾼 칸(scan_started_at/scan_done)을 돌려준다."""
    base_url, token = repository.get_setting(_SERVER_URL_KEY) or "", get_token()
    if not state["scan_started_at"]:
        result = await webtoon_server_client.start_scan(session, base_url, token, timeout_seconds)
        if result in ("started", "running"):  # running = 이미 스캔 중(자동 재스캔 등) — 그 스캔이 끝나기를 기다린다
            job_status.log_line("report", "뷰어 서버 스캔을 시작했습니다" if result == "started" else "뷰어 서버가 이미 스캔 중이라 그 스캔이 끝나기를 기다립니다")
            return {"scan_started_at": now.astimezone(timezone.utc).isoformat()}
        job_status.log_line("report", "뷰어 서버 스캔을 시킬 수 없어서(연동 미지원/인증 실패/연결 오류) 건너뜁니다 — 리포트는 그대로 보냅니다")
        return {"scan_done": True}
    if now - datetime.fromisoformat(state["scan_started_at"]) >= timedelta(minutes=get_max_wait_minutes()):
        job_status.log_line("report", f"뷰어 서버 스캔이 {get_max_wait_minutes()}분 안에 끝나지 않아 기다리지 않고 리포트를 보냅니다")
        return {"scan_done": True}
    status = await webtoon_server_client.scan_status(session, base_url, token, timeout_seconds)
    if status is not None and not status.get("running"):
        job_status.log_line("report", "뷰어 서버 스캔이 끝났습니다")
        return {"scan_done": True}
    return {}
