"""웹툰 뷰어 서버(webtoon-server)에 스캔을 시키는 공용 조정 장치 — 다운로드(수동 다운로드 포함)와 아카이빙(자동/수동/일괄 이동)이 새 파일을 만들면
뷰어가 다시 훑게 해서, 뷰어 화면과 리포트의 "바로가기" 링크가 새 파일을 반영하게 한다.

약속: "변경 시각 이후에 시작한 스캔이 끝나야 그 변경이 반영된다."
- request(when): 스캔이 필요한 변경을 기록한다(연동이 켜져 있고 준비된 경우에만). 이미 그 변경 뒤에 시작한 스캔이 있으면 따로 요청하지 않는다(같은 변경에 스캔을 두 번 시키지 않음).
- tick(): 1분마다(report_tick) 한 번 — 우리가 시작한 스캔은 끝났는지 상태만 확인하고(서버에는 가벼운 조회), 필요하면 새로 시작한다. 스캔 도중에 새 변경이 생기면 그 스캔이 끝난 뒤
  다시 시작한다. 뷰어가 이미 스캔 중이면(자동 재스캔 등 우리가 시작하지 않은 스캔) 끝나기를 기다렸다가 시작한다(그 스캔은 우리 변경 전에 시작했을 수 있다).
- 네트워크 드라이브는 스캔이 15~30분 걸릴 수 있어서 요청을 오래 붙잡지 않는다: 시작 요청(POST /api/integration/rescan)은 바로 돌아오고 이후 진행 상태(GET /api/integration/scan-status)만 본다.
  최대 대기 시간(기본 45분)이 지나면 추적을 멈춘다. 뷰어가 이 연동을 지원하지 않거나(404) 토큰이 안 맞거나(401·403) 연결 오류면 그 요청은 버리고 다시 시도하지 않는다.
- 뷰어는 로그인(PROFILES_ENABLED)이 켜져 있어도 쓸 수 있게 별도 토큰(뷰어의 INTEGRATION_TOKEN)으로 인증한다 — 토큰은 암호화해서 저장한다.
상태는 설정 테이블에 JSON 한 덩어리로 저장해서 재시작해도 이어진다. 진행 로그는 리포트 상태 로그("report")에 남는다.
"""
import json
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


# ── 요청/진행 상태 ───────────────────────────────────────────────────

STATE_KEY = "viewer_scan_state"


def _empty_state() -> dict:
    return {
        "requested_at": None,  # 스캔이 필요한 가장 최근 변경 시각(아직 그 뒤에 시작한 스캔이 없음). None = 필요한 것 없음
        "started_at": None,  # 마지막으로 시작한 스캔의 시작 시각
        "finished": True,  # 그 스캔이 끝났는지(또는 추적을 멈췄는지)
        "done_started_at": None,  # 끝까지 확인된 가장 최근 스캔의 시작 시각 — 이 시각 이전의 변경은 반영됐다
        "foreign_wait": False,  # 뷰어가 이미 스캔 중이라(우리가 시작한 게 아님) 끝나기를 기다리는 중
        "watermark": None,  # 다운로드 묶음을 여기까지는 스캔 대상으로 따졌다(같은 이력을 다시 따지지 않게)
    }


def load_state() -> dict:
    raw = repository.get_setting(STATE_KEY)
    try:
        return {**_empty_state(), **(json.loads(raw) if raw else {})}
    except ValueError:
        return _empty_state()


def update_state(**fields) -> dict:
    state = {**load_state(), **fields}
    repository.set_setting(STATE_KEY, json.dumps(state))
    return state


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def has_work() -> bool:
    """tick이 할 일이 있다: 스캔이 필요한 변경이 있거나, 우리가 시작한 스캔이 아직 도는 중이다(준비가 안 됐으면 없다)."""
    if not is_ready():
        return False
    state = load_state()
    return bool(state["requested_at"]) or (bool(state["started_at"]) and not state["finished"])


def request(when: datetime) -> None:
    """when 시각에 뷰어가 훑어야 할 변경(새로 받거나 옮긴 파일)이 생겼다고 기록한다 — 연동이 준비돼 있을 때만."""
    if not is_ready():
        return
    state = load_state()
    when_iso = _iso(when)
    if state["started_at"] and state["started_at"] >= when_iso:
        return  # 이미 그 변경 뒤에 시작한 스캔이 있다 — 그 스캔이 덮는다
    if state["requested_at"] is None or state["requested_at"] < when_iso:
        update_state(requested_at=when_iso)


def note_library_changed(count: int, when: datetime | None = None) -> None:
    """파일을 옮기는 작업(아카이빙/일괄 이동)이 끝났을 때 부른다 — 하나라도 옮겼으면 스캔을 요청한다."""
    if count > 0:
        request(when or datetime.now(timezone.utc))


def set_watermark(when: datetime) -> None:
    update_state(watermark=_iso(when))


def covered_state(when: datetime) -> str:
    """when 시각의 변경이 뷰어에 반영됐나: "done"(그 뒤에 시작한 스캔이 끝남) / "waiting"(스캔 중이거나 곧 시작) / "failed"(스캔을 시킬 수 없었거나 포기함)."""
    state = load_state()
    when_iso = _iso(when)
    if state["done_started_at"] and state["done_started_at"] >= when_iso:
        return "done"
    if state["started_at"] and state["started_at"] >= when_iso and not state["finished"]:
        return "waiting"
    return "waiting" if state["requested_at"] else "failed"


async def tick(session, now: datetime, timeout_seconds: int) -> None:
    """한 걸음: 우리가 시작한 스캔이 끝났는지 확인하고(끝나기 전에는 새로 시작하지 않는다), 필요하면 시작한다. 한 번에 상태 조회 한 번 + 시작 요청 한 번 이하."""
    if not is_ready():
        return
    now = now.astimezone(timezone.utc)
    state = load_state()
    base_url, token, max_wait = repository.get_setting(_SERVER_URL_KEY) or "", get_token(), timedelta(minutes=get_max_wait_minutes())
    if state["requested_at"] and state["started_at"] and state["started_at"] >= state["requested_at"]:
        state = update_state(requested_at=None)  # 이미 덮인 요청

    if state["started_at"] and not state["finished"]:  # 우리가 시작한 스캔이 도는 중
        if now - datetime.fromisoformat(state["started_at"]) >= max_wait:
            job_status.log_line("report", f"뷰어 서버 스캔이 {get_max_wait_minutes()}분 안에 끝나지 않아 더 이상 추적하지 않습니다")
            state = update_state(finished=True)
        else:
            status = await webtoon_server_client.scan_status(session, base_url, token, timeout_seconds)
            if status is None or status.get("running"):
                return  # 아직 도는 중 — 새 스캔은 끝난 뒤에
            job_status.log_line("report", "뷰어 서버 스캔이 끝났습니다")
            state = update_state(finished=True, done_started_at=state["started_at"])

    if not state["requested_at"]:
        return
    if state["foreign_wait"]:  # 뷰어가 다른 이유로 스캔 중 — 끝나면 시작
        if now - datetime.fromisoformat(state["requested_at"]) >= max_wait:
            job_status.log_line("report", f"뷰어 서버의 스캔이 {get_max_wait_minutes()}분 안에 끝나지 않아 스캔 요청을 건너뜁니다")
            update_state(requested_at=None, foreign_wait=False)
            return
        status = await webtoon_server_client.scan_status(session, base_url, token, timeout_seconds)
        if status is None or status.get("running"):
            return
        state = update_state(foreign_wait=False)
    result = await webtoon_server_client.start_scan(session, base_url, token, timeout_seconds)
    if result == "started":
        job_status.log_line("report", "뷰어 서버 스캔을 시작했습니다")
        update_state(started_at=_iso(now), finished=False, requested_at=None)
    elif result == "running":
        job_status.log_line("report", "뷰어 서버가 이미 스캔 중이라 그 스캔이 끝난 뒤에 시작합니다")
        update_state(foreign_wait=True)
    else:
        job_status.log_line("report", "뷰어 서버 스캔을 시킬 수 없어서(연동 미지원/인증 실패/연결 오류) 건너뜁니다")
        update_state(requested_at=None)

