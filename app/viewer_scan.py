"""웹툰 뷰어 서버(webtoon-server)에 스캔을 시키는 공용 조정 장치 — 다운로드(수동 다운로드 포함)와 아카이빙(자동/수동/일괄 이동)이 새 파일을 만들면
뷰어가 다시 훑게 해서, 뷰어 화면과 리포트의 "바로가기" 링크가 새 파일을 반영하게 한다.

저장소 범위(scope): 다운로드는 로컬 폴더에 떨어지므로 "fast"(로컬 플랫폼)만, 아카이빙·일괄 이동은 로컬에서 빠지고 원격 드라이브에 들어가므로 "all"(로컬+원격).
뷰어는 로컬(fast)과 원격(slow) 스캔을 서로 다른 작업으로 동시에 돌리므로 여기서도 두 레인(lane)을 따로 추적한다 — 원격 스캔이 30분째 돌고 있어도 로컬 스캔은
바로 시작해 몇 분 안에 끝나고, 다운로드 리포트는 로컬 레인만 기다린다. 옛 뷰어(범위를 모름: 응답에 scope가 없다)면 전체 스캔 하나로 합쳐서 예전처럼 동작한다(legacy).

약속: "변경 시각 이후에 시작한 (그 범위의) 스캔이 끝나야 그 변경이 반영된다."
- request(when, scope): 스캔이 필요한 변경을 레인마다 기록한다(연동이 켜져 있고 준비된 경우에만). 이미 그 변경 뒤에 시작한 스캔이 있으면 따로 요청하지 않는다(같은 변경에 스캔을 두 번 시키지 않음).
- tick(): 1분마다(report_tick) 한 번 — 레인마다 우리가 시작한 스캔은 끝났는지 상태만 확인하고(서버에는 가벼운 조회), 필요하면 새로 시작한다. 스캔 도중에 새 변경이 생기면 그 스캔이 끝난 뒤
  다시 시작한다. 뷰어가 이미 그 범위를 스캔 중이면(자동 재스캔 등 우리가 시작하지 않은 스캔) 끝나기를 기다렸다가 시작한다(그 스캔은 우리 변경 전에 시작했을 수 있다).
- 네트워크 드라이브는 스캔이 15~30분 걸릴 수 있어서 요청을 오래 붙잡지 않는다: 시작 요청(POST /api/integration/rescan)은 바로 돌아오고 이후 진행 상태(GET /api/integration/scan-status)만 본다.
  최대 대기 시간(기본 45분)이 지나면 추적을 멈춘다. 뷰어가 이 연동을 지원하지 않거나(404) 토큰이 안 맞거나(401·403) 연결 오류면 그 요청은 버리고 다시 시도하지 않는다.
- 뷰어는 로그인(PROFILES_ENABLED)이 켜져 있어도 쓸 수 있게 별도 토큰(뷰어의 INTEGRATION_TOKEN)으로 인증한다 — 토큰은 암호화해서 저장한다.
상태는 설정 테이블에 JSON 한 덩어리로 저장해서 재시작해도 이어진다. 진행 로그는 실행 이력의 "웹툰 서버 연동"(viewer_server)에 스캔 주기(요청 → 시작 → 끝/건너뜀/추적 중단)마다 한 건으로 남고,
줄마다 어느 저장소인지([로컬]/[원격]/[전체]) 붙는다.
"""
import logging
from datetime import datetime, timedelta, timezone

from app import job_status, repository, webtoon_server_client
from app.json_setting import JsonSetting
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
LANES = ("fast", "slow")  # 뷰어의 스캔 범위와 같다: fast=로컬 플랫폼, slow=SLOW_PLATFORMS(네트워크 드라이브)
SCOPES = LANES + ("all",)
_LABEL = {"fast": "로컬", "slow": "원격", "all": "로컬+원격", None: "전체"}  # None = 옛 뷰어의 전체 스캔 하나


def _empty_lane() -> dict:
    return {
        "requested_at": None,  # 스캔이 필요한 가장 최근 변경 시각(아직 그 뒤에 시작한 스캔이 없음). None = 필요한 것 없음
        "started_at": None,  # 마지막으로 시작한 스캔의 시작 시각
        "finished": True,  # 그 스캔이 끝났는지(또는 추적을 멈췄는지)
        "done_started_at": None,  # 끝까지 확인된 가장 최근 스캔의 시작 시각 — 이 시각 이전의 변경은 반영됐다
        "foreign_wait": False,  # 뷰어가 이미 스캔 중이라(우리가 시작한 게 아님) 끝나기를 기다리는 중
    }


def _empty_state() -> dict:
    return {
        "lanes": {lane: _empty_lane() for lane in LANES},
        "legacy": None,  # 뷰어가 범위(scope)를 아는지: None=아직 모름, True=옛 뷰어(전체 스캔 하나 — fast 레인 하나로 합친다), False=범위별로 돈다
        "watermark": None,  # 다운로드 묶음을 여기까지는 스캔 대상으로 따졌다(같은 이력을 다시 따지지 않게)
        "cycle_failed": False,  # 지금 스캔 주기(실행 이력 한 건)에서 건너뛰거나 추적을 포기한 일이 있었다 — 주기가 끝날 때 오류로 남긴다
    }


# 상태는 설정에 JSON 한 덩어리로 저장한다 — 아카이빙 쪽 알림(request)과 1분 확인(tick)이 다른 스레드에서 겹쳐도 서로의 갱신을 지우지 않게 JsonSetting이 잠금으로 보호한다.
_STATE = JsonSetting(STATE_KEY, _empty_state)


def load_state() -> dict:
    """저장된 상태. 레인 없이 저장된 옛 모양(requested_at 등이 맨 위에 있음)은 빈 레인으로 다룬다(진행 중이던 스캔 추적은 이어지지 않지만 깨지지 않는다)."""
    state = _STATE.load()
    if not isinstance(state.get("lanes"), dict):
        state["lanes"] = {}
    for lane in LANES:
        state["lanes"][lane] = {**_empty_lane(), **(state["lanes"].get(lane) or {})}
    return state


def update_state(**fields) -> dict:
    with _STATE.lock:
        state = {**load_state(), **fields}
        _STATE.save(state)
        return state


def _update_lane(lane: str, **fields) -> dict:
    """레인 하나의 필드만 바꾼다(다른 레인과 공통 필드는 그대로)."""
    with _STATE.lock:
        state = load_state()
        state["lanes"][lane] = {**state["lanes"][lane], **fields}
        _STATE.save(state)
        return state


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def _lanes_for(scope: str, state: dict) -> tuple[str, ...]:
    """이 범위가 실제로 쓰는 레인들 — 옛 뷰어(legacy)는 전체 스캔 하나라 모든 범위가 fast 레인 하나를 쓴다."""
    if state.get("legacy"):
        return ("fast",)
    return LANES if scope == "all" else (scope,)


def _lane_has_work(lane: dict) -> bool:
    return bool(lane["requested_at"]) or (bool(lane["started_at"]) and not lane["finished"])


def has_work() -> bool:
    """tick이 할 일이 있다: 어느 레인이든 스캔이 필요한 변경이 있거나, 우리가 시작한 스캔이 아직 도는 중이다(준비가 안 됐으면 없다)."""
    if not is_ready():
        return False
    return any(_lane_has_work(lane) for lane in load_state()["lanes"].values())


# ── 실행 이력("웹툰 서버 연동") — 스캔 주기 하나(요청 ~ 끝/건너뜀/추적 중단)가 이력 한 건 ──
_JOB = "viewer_server"


def _cycle_log(line: str) -> None:
    """스캔 주기의 진행 줄을 남긴다 — 진행 중인 주기가 없으면(처음이거나 컨테이너가 다시 시작됐다) 새로 시작한다."""
    if job_status.snapshot()[_JOB]["status"] != "running":
        job_status.start(_JOB)
    job_status.log_line(_JOB, line)


def _lane_log(lane: str, state: dict, line: str) -> None:
    """레인의 진행 줄 — 어느 저장소인지 앞에 붙인다([로컬]/[원격], 옛 뷰어는 [전체])."""
    _cycle_log(f"[{_LABEL[None if state.get('legacy') else lane]}] {line}")


def _end_cycle_if_idle() -> None:
    """모든 레인이 할 일이 없을 때 스캔 주기를 끝내고 이력으로 저장한다(건너뛰거나 추적을 포기한 일이 있었으면 오류). 진행 중인 주기가 없으면 아무것도 안 한다."""
    if job_status.snapshot()[_JOB]["status"] != "running":
        return
    state = load_state()
    if any(_lane_has_work(lane) for lane in state["lanes"].values()):
        return
    update_state(cycle_failed=False)
    job_status.finish(_JOB, success=not state.get("cycle_failed"))


def request(when: datetime, *, scope: str, reason: str = "") -> None:
    """when 시각에 뷰어가 훑어야 할 변경(새로 받거나 옮긴 파일)이 생겼다고 기록한다 — 연동이 준비돼 있을 때만.
    scope: "fast"(로컬 폴더에 새 파일 — 다운로드) / "all"(로컬에서 빠지고 원격에 들어감 — 아카이빙·일괄 이동) / "slow". reason: 실행 이력에 남길 사유(어디서 생긴 변경인지)."""
    if scope not in SCOPES:
        raise ValueError(f"알 수 없는 스캔 범위: {scope}")
    if not is_ready():
        return
    with _STATE.lock:
        state = load_state()
        when_iso = _iso(when)
        recorded = False
        for lane_name in _lanes_for(scope, state):
            lane = state["lanes"][lane_name]
            if lane["started_at"] and lane["started_at"] >= when_iso:
                continue  # 이미 그 변경 뒤에 시작한 스캔이 있다 — 그 스캔이 덮는다
            if lane["requested_at"] is None or lane["requested_at"] < when_iso:
                lane["requested_at"] = when_iso
                recorded = True
        if recorded:
            _STATE.save(state)
            _cycle_log(f"스캔 필요[{_LABEL[None if state.get('legacy') else scope]}]: {reason or '새 파일'}")


def note_library_changed(count: int, when: datetime | None = None, source: str = "아카이빙") -> None:
    """파일을 옮기는 작업(아카이빙/일괄 이동)이 끝났을 때 부른다 — 하나라도 옮겼으면 로컬+원격 스캔을 요청한다(로컬에서 빠진 파일 정리 + 원격에 들어간 파일 반영).
    source: 실행 이력에 남길 출처(정기 아카이빙, 지금 이동, 폴더 일괄 이동 등)."""
    if count > 0:
        request(when or datetime.now(timezone.utc), scope="all", reason=f"{source}에서 파일 {count}개를 옮김")


def set_watermark(when: datetime) -> None:
    update_state(watermark=_iso(when))


def _lane_covered_state(lane: dict, when_iso: str) -> str:
    if lane["done_started_at"] and lane["done_started_at"] >= when_iso:
        return "done"
    if lane["started_at"] and lane["started_at"] >= when_iso and not lane["finished"]:
        return "waiting"
    return "waiting" if lane["requested_at"] else "failed"


def covered_state(when: datetime, scope: str) -> str:
    """when 시각의 변경이 이 범위의 뷰어 스캔에 반영됐나: "done"(그 뒤에 시작한 스캔이 끝남) / "waiting"(스캔 중이거나 곧 시작) / "failed"(스캔을 시킬 수 없었거나 포기함).
    all은 두 레인이 모두 끝나야 done, 하나라도 기다리는 중이면 waiting."""
    state = load_state()
    when_iso = _iso(when)
    states = [_lane_covered_state(state["lanes"][lane], when_iso) for lane in _lanes_for(scope, state)]
    if "waiting" in states:
        return "waiting"
    return "done" if all(s == "done" for s in states) else "failed"


def _collapse_to_single_lane(state: dict, started_lane: str) -> dict:
    """옛 뷰어임을 알았다: 방금 시작한 스캔은 전체를 훑으므로 다른 레인의 대기 중 요청도 그 스캔이 덮는다 — 그 레인의 상태를 fast 레인으로 옮기고 나머지는 비운다."""
    merged = state["lanes"][started_lane]
    state["lanes"] = {lane: _empty_lane() for lane in LANES}
    state["lanes"]["fast"] = merged
    state["legacy"] = True
    _STATE.save(state)
    return state


async def tick(session, now: datetime, timeout_seconds: int) -> None:
    """한 걸음: 레인마다 우리가 시작한 스캔이 끝났는지 확인하고(끝나기 전에는 새로 시작하지 않는다), 필요하면 시작한다. 레인마다 상태 조회 한 번 + 시작 요청 한 번 이하."""
    if not is_ready():
        return
    now = now.astimezone(timezone.utc)
    base_url, token = repository.get_setting(_SERVER_URL_KEY) or "", get_token()
    for lane_name in LANES:
        state = load_state()
        if lane_name not in _lanes_for("all", state):
            break  # 옛 뷰어: fast 레인 하나만
        if _lane_has_work(state["lanes"][lane_name]):
            await _tick_lane(session, now, timeout_seconds, base_url, token, lane_name)
    _end_cycle_if_idle()


async def _tick_lane(session, now: datetime, timeout_seconds: int, base_url: str, token: str, lane_name: str) -> None:
    state = load_state()
    lane = state["lanes"][lane_name]
    max_wait = timedelta(minutes=get_max_wait_minutes())
    scope = "all" if state.get("legacy") else lane_name
    if lane["requested_at"] and lane["started_at"] and lane["started_at"] >= lane["requested_at"]:
        state = _update_lane(lane_name, requested_at=None); lane = state["lanes"][lane_name]  # 이미 덮인 요청

    if lane["started_at"] and not lane["finished"]:  # 우리가 시작한 스캔이 도는 중
        elapsed = now - datetime.fromisoformat(lane["started_at"])
        if elapsed >= max_wait:
            _lane_log(lane_name, state, f"웹툰 서버 스캔이 {get_max_wait_minutes()}분 안에 끝나지 않아 더 이상 추적하지 않습니다")
            state = _update_lane(lane_name, finished=True); update_state(cycle_failed=True); lane = state["lanes"][lane_name]
        else:
            status = await webtoon_server_client.scan_status(session, base_url, token, timeout_seconds, scope)
            if status is None or status.get("running"):
                return  # 아직 도는 중 — 새 스캔은 끝난 뒤에(상태 확인은 이력에 줄을 남기지 않는다)
            _lane_log(lane_name, state, f"스캔이 끝났습니다 (시작 후 {int(elapsed.total_seconds() // 60)}분)")
            state = _update_lane(lane_name, finished=True, done_started_at=lane["started_at"]); lane = state["lanes"][lane_name]

    if not lane["requested_at"]:
        return  # 이 레인은 끝났고 대기 중인 요청도 없다
    if lane["foreign_wait"]:  # 뷰어가 다른 이유로 이 범위를 스캔 중 — 끝나면 시작
        if now - datetime.fromisoformat(lane["requested_at"]) >= max_wait:
            _lane_log(lane_name, state, f"웹툰 서버의 스캔이 {get_max_wait_minutes()}분 안에 끝나지 않아 스캔 요청을 건너뜁니다")
            _update_lane(lane_name, requested_at=None, foreign_wait=False); update_state(cycle_failed=True)
            return
        status = await webtoon_server_client.scan_status(session, base_url, token, timeout_seconds, scope)
        if status is None or status.get("running"):
            return
        state = _update_lane(lane_name, foreign_wait=False)
    result, supports_scope = await webtoon_server_client.start_scan(session, base_url, token, timeout_seconds, scope)
    if result in ("started", "running") and state.get("legacy") != (not supports_scope):  # 뷰어가 범위를 아는지 처음 알았거나(또는 뷰어를 바꿨다) — 다음 줄들의 표시와 레인 사용이 여기 따른다
        if supports_scope:
            state = update_state(legacy=False)
        else:
            state = _collapse_to_single_lane(state, lane_name)
            lane_name = "fast"
    if result == "started":
        _lane_log(lane_name, state, "웹툰 서버에 스캔을 시작시켰습니다")
        _update_lane(lane_name, started_at=_iso(now), finished=False, requested_at=None)
    elif result == "running":
        _lane_log(lane_name, state, "웹툰 서버가 이미 스캔 중이라 끝난 뒤 시작합니다")
        _update_lane(lane_name, foreign_wait=True)
    else:
        reason = "연동을 쓸 수 없어(연동 미지원, 주소·토큰 확인 필요)" if result == "unsupported" else "연결하지 못해"
        _lane_log(lane_name, state, f"웹툰 서버에 {reason} 스캔을 건너뜁니다")
        _update_lane(lane_name, requested_at=None); update_state(cycle_failed=True)
