"""
webtoons/settings 테이블에 대한 유일한 접근 경로.

다른 모듈(tracker/scheduler/api)은 여기 정의된 함수만 호출하고, sqlite3나
SQL을 직접 다루지 않는다 (SRP). 모든 함수는 동기(sync)이며, 비동기 코드에서
호출할 때는 호출부에서 asyncio.to_thread로 감싼다.
"""

import json
from datetime import datetime, timezone

from app.db import get_connection, write_transaction
from app.models import WebtoonRecord

STATUS_ACTIVE = "active"
STATUS_UNSUBSCRIBED = "unsubscribed"
STATUS_EXCLUDED = "excluded"

SOURCE_MANUAL = "manual"
SOURCE_ARTIST = "artist"
SOURCE_TAG = "tag"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row) -> WebtoonRecord:
    return WebtoonRecord(
        title_id=row["title_id"],
        title=row["title"],
        status=row["status"],
        is_adult=bool(row["is_adult"]),
        writer_ids=json.loads(row["writer_ids"] or "[]"),
        added_source=row["added_source"],
        last_downloaded_no=row["last_downloaded_no"],
        is_finished=bool(row["is_finished"]),
        finish_ack=bool(row["finish_ack"]),
        thumbnail_url=row["thumbnail_url"] or "",
        finish_notified=bool(row["finish_notified"]),
    )


def list_all() -> list[WebtoonRecord]:
    rows = get_connection().execute("SELECT * FROM webtoons ORDER BY title").fetchall()
    return [_row_to_record(r) for r in rows]


def list_by_status(status: str) -> list[WebtoonRecord]:
    rows = get_connection().execute(
        "SELECT * FROM webtoons WHERE status = ? ORDER BY title", (status,)
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def get(title_id: str) -> WebtoonRecord | None:
    row = get_connection().execute(
        "SELECT * FROM webtoons WHERE title_id = ?", (title_id,)
    ).fetchone()
    return _row_to_record(row) if row else None


def exists(title_id: str) -> bool:
    row = get_connection().execute(
        "SELECT 1 FROM webtoons WHERE title_id = ?", (title_id,)
    ).fetchone()
    return row is not None


def upsert_new(
    title_id: str,
    title: str,
    is_adult: bool = False,
    writer_ids: list[str] | None = None,
    added_source: str = SOURCE_MANUAL,
    thumbnail_url: str = "",
) -> None:
    """이미 존재하면 아무 것도 하지 않는다 (구독 취소/제외 상태를 덮어쓰지 않기 위해)."""
    if exists(title_id):
        return
    now = _now()
    with write_transaction() as conn:
        conn.execute(
            """
            INSERT INTO webtoons
                (title_id, title, status, is_adult, writer_ids, added_source,
                 last_downloaded_no, is_finished, finish_ack, thumbnail_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (
                title_id,
                title,
                STATUS_ACTIVE,
                int(is_adult),
                json.dumps(writer_ids or []),
                added_source,
                thumbnail_url,
                now,
                now,
            ),
        )


def update_thumbnail_url(title_id: str, thumbnail_url: str) -> None:
    if not thumbnail_url:
        return
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET thumbnail_url = ?, updated_at = ? WHERE title_id = ?",
            (thumbnail_url, _now(), title_id),
        )


def set_status(title_id: str, status: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET status = ?, updated_at = ? WHERE title_id = ?",
            (status, _now(), title_id),
        )


def update_last_downloaded_no(title_id: str, episode_no: int) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET last_downloaded_no = ?, updated_at = ? WHERE title_id = ?",
            (episode_no, _now(), title_id),
        )


def update_is_adult(title_id: str, is_adult: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET is_adult = ?, updated_at = ? WHERE title_id = ?",
            (int(is_adult), _now(), title_id),
        )


def update_writer_ids(title_id: str, writer_ids: list[str]) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET writer_ids = ?, updated_at = ? WHERE title_id = ?",
            (json.dumps(writer_ids), _now(), title_id),
        )


def mark_finished(title_id: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET is_finished = 1, updated_at = ? WHERE title_id = ?",
            (_now(), title_id),
        )


def acknowledge_finish(title_id: str) -> None:
    """알람 제외: 구독은 그대로 유지하고 완결 알림만 그만 받는다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET finish_ack = 1, updated_at = ? WHERE title_id = ?",
            (_now(), title_id),
        )


def set_finish_notified(title_id: str) -> None:
    """완결 확인 디스코드 메시지를 보냈음을 기록한다 (중복 알림 방지용)."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET finish_notified = 1, updated_at = ? WHERE title_id = ?",
            (_now(), title_id),
        )


def hard_delete(title_id: str) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM webtoons WHERE title_id = ?", (title_id,))


def get_setting(key: str) -> str | None:
    row = get_connection().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with write_transaction() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
