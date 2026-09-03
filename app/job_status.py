"""
잡(discovery/download/manual/registry) 진행 상황을 메모리에 잠깐 보관한다 — 실시간
폴링(설정 페이지가 2초마다 조회)용이라 컨테이너 재시작하면 사라져도 무방하다.

실행이 끝나면(finish) 그 실행의 결과(성공/실패 + 로그 전체)를 repository의
job_history 테이블에도 남긴다 — 스케줄대로 자동 실행된 잡이 실제로 돌았는지,
성공했는지를 나중에(설정 페이지를 안 보고 있었어도) 확인할 수 있어야 하기 때문이다.
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
    "manual": _JobStatus(),
    "registry": _JobStatus(),
    "metadata_sync": _JobStatus(),
    "report": _JobStatus(),
    "archive": _JobStatus(),
    "bulk_move": _JobStatus(),
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
        started_at, finished_at, log_copy = st.started_at, st.finished_at, list(st.log)

    # DB 쓰기는 락 밖에서 — repository의 write_transaction이 자체적으로 직렬화하므로
    # job_status의 락을 오래 붙잡을 필요가 없다.
    from app import repository  # 순환 import 방지용 지연 import

    try:
        repository.add_job_history(job_name, started_at or finished_at, finished_at, st.status, log_copy)
    except Exception:
        pass  # 이력 저장 실패가 잡 자체의 실행 결과에 영향을 주면 안 됨


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
