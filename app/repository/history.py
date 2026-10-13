"""실행 이력(job_history)과 회차 다운로드 이력(episode_history).
"""

import json
from datetime import datetime, timedelta, timezone

from app.db import fetchall, read_lock, write_transaction

from app.repository.common import (
    _now,
)

# ── job_history (스케줄 실행 이력) ───────────────────────────────

_JOB_HISTORY_KEEP_PER_JOB = 30


def add_job_history(job_name: str, started_at: str, finished_at: str, status: str, log: list[str]) -> None:
    with write_transaction() as conn:
        conn.execute(
            "INSERT INTO job_history (job_name, started_at, finished_at, status, log) VALUES (?, ?, ?, ?, ?)",
            (job_name, started_at, finished_at, status, json.dumps(log)),
        )
        # 잡마다 최근 N개만 남기고 오래된 이력은 정리한다.
        conn.execute(
            """
            DELETE FROM job_history
            WHERE job_name = ? AND id NOT IN (
                SELECT id FROM job_history WHERE job_name = ? ORDER BY started_at DESC LIMIT ?
            )
            """,
            (job_name, job_name, _JOB_HISTORY_KEEP_PER_JOB),
        )


def list_job_history(limit_per_job: int = 10) -> list[dict]:
    with read_lock() as conn:
        job_names = [r["job_name"] for r in conn.execute("SELECT DISTINCT job_name FROM job_history").fetchall()]
        results: list[dict] = []
        for job_name in job_names:
            rows = conn.execute(
                "SELECT * FROM job_history WHERE job_name = ? ORDER BY started_at DESC LIMIT ?",
                (job_name, limit_per_job),
            ).fetchall()
            for r in rows:
                results.append(
                    {
                        "id": r["id"],
                        "job_name": r["job_name"],
                        "started_at": r["started_at"],
                        "finished_at": r["finished_at"],
                        "status": r["status"],
                        "log": json.loads(r["log"] or "[]"),
                    }
                )
    results.sort(key=lambda r: r["started_at"], reverse=True)
    return results


def delete_job_history_entry(entry_id: int) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM job_history WHERE id = ?", (entry_id,))


def clear_job_history() -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM job_history")


def delete_job_history_older_than(days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with write_transaction() as conn:
        cursor = conn.execute("DELETE FROM job_history WHERE started_at < ?", (cutoff,))
        return cursor.rowcount


# ── episode_history (회차 단위 다운로드 이력) ──────────────────────

def add_episode_history(
    title_id: str, title_name: str, episode_no: int, subtitle: str, status: str, error_msg: str = "",
    platform: str = "naver",
) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            INSERT INTO episode_history
                (title_id, title_name, episode_no, subtitle, status, error_msg, downloaded_at, platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title_id, title_name, episode_no, subtitle, status, error_msg, _now(), platform),
        )


def list_episode_history(
    status: str | None = None, search: str = "", page: int = 1, page_size: int = 30
) -> tuple[list[dict], int]:
    """(행 목록, 전체 개수)를 반환한다 — 상태/제목 검색 필터 + 페이지네이션."""
    where_clauses = []
    params: list = []
    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if search:
        where_clauses.append("(title_name LIKE ? OR subtitle LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern])
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with read_lock() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM episode_history {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM episode_history {where_sql} ORDER BY downloaded_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()

    result = [
        {
            "id": r["id"],
            "title_id": r["title_id"],
            "title_name": r["title_name"],
            "episode_no": r["episode_no"],
            "subtitle": r["subtitle"],
            "status": r["status"],
            "error_msg": r["error_msg"],
            "downloaded_at": r["downloaded_at"],
            "platform": r["platform"],
        }
        for r in rows
    ]
    return result, total


def delete_episode_history(entry_id: int) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM episode_history WHERE id = ?", (entry_id,))


def clear_episode_history() -> None:
    """이력만 지운다 (다운로드된 파일은 그대로 유지됨)."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM episode_history")


def list_episode_history_since(since_iso: str) -> list[dict]:
    """지정 시각 이후에 기록된 모든 회차 이력을 반환한다 (성공/실패 전부, 페이지네이션 없음) —
    리포트 발송용으로, 지난 발송 이후 구간을 통째로 훑을 때 쓴다."""
    rows = fetchall(
        "SELECT * FROM episode_history WHERE downloaded_at > ? ORDER BY downloaded_at ASC",
        (since_iso,),
    )
    return [
        {
            "id": r["id"],
            "title_id": r["title_id"],
            "title_name": r["title_name"],
            "episode_no": r["episode_no"],
            "subtitle": r["subtitle"],
            "status": r["status"],
            "error_msg": r["error_msg"],
            "downloaded_at": r["downloaded_at"],
            "platform": r["platform"],
        }
        for r in rows
    ]


def list_episode_history_between(start_iso: str, end_iso: str) -> list[dict]:
    """[start_iso, end_iso) 구간의 이력을 반환한다 — 리포트 테스트 발송에서 "오늘"
    또는 "어제" 하루치만 정확히 뽑아낼 때 쓴다."""
    rows = fetchall(
        "SELECT * FROM episode_history WHERE downloaded_at >= ? AND downloaded_at < ? ORDER BY downloaded_at ASC",
        (start_iso, end_iso),
    )
    return [
        {
            "id": r["id"],
            "title_id": r["title_id"],
            "title_name": r["title_name"],
            "episode_no": r["episode_no"],
            "subtitle": r["subtitle"],
            "status": r["status"],
            "error_msg": r["error_msg"],
            "downloaded_at": r["downloaded_at"],
            "platform": r["platform"],
        }
        for r in rows
    ]


def delete_episode_history_older_than(days: int) -> int:
    """다운로드된 지 N일 넘은 이력을 지운다 (파일은 그대로 유지됨). 지운 개수를 반환."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with write_transaction() as conn:
        cursor = conn.execute("DELETE FROM episode_history WHERE downloaded_at < ?", (cutoff,))
        return cursor.rowcount
