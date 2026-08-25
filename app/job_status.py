"""
잡(discovery/download/commands) 진행 상황을 메모리에 잠깐 보관한다.

DB에 넣지 않는 이유: 이건 "지금 이 컨테이너가 뭘 하고 있는지"를 보여주는
운영 상태일 뿐 영속시킬 필요가 없는 값이라, 컨테이너 재시작하면 사라져도
무방하다 (오히려 재시작 후에도 지난 로그가 남아있는 게 더 헷갈린다).
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

_MAX_LOG_LINES = 300
_lock = threading.Lock()


@dataclass
class _JobStatus:
    status: str = "idle"  # idle | running | success | error
    started_at: str | None = None
    finished_at: str | None = None
    log: list[str] = field(default_factory=list)


_statuses: dict[str, _JobStatus] = {
    "discovery": _JobStatus(),
    "download": _JobStatus(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start(job_name: str) -> None:
    with _lock:
        st = _statuses[job_name]
        st.status = "running"
        st.started_at = _now()
        st.finished_at = None
        st.log = []


def log_line(job_name: str, line: str) -> None:
    with _lock:
        st = _statuses[job_name]
        st.log.append(f"{_now()} — {line}")
        if len(st.log) > _MAX_LOG_LINES:
            st.log = st.log[-_MAX_LOG_LINES:]


def finish(job_name: str, success: bool) -> None:
    with _lock:
        st = _statuses[job_name]
        st.status = "success" if success else "error"
        st.finished_at = _now()


def snapshot() -> dict:
    with _lock:
        return {
            name: {
                "status": st.status,
                "started_at": st.started_at,
                "finished_at": st.finished_at,
                "log": list(st.log),
            }
            for name, st in _statuses.items()
        }
