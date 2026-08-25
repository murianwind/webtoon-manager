"""
webtoons/settings/watched_authors/watched_tags 테이블에 대한 유일한 접근 경로.

다른 모듈(tracker/scheduler/api)은 여기 정의된 함수만 호출하고, sqlite3나
SQL을 직접 다루지 않는다 (SRP). 모든 함수는 동기(sync)이며, 비동기 코드에서
호출할 때는 호출부에서 asyncio.to_thread로 감싼다.
"""

import json
from datetime import datetime, timezone

from app.db import get_connection, write_transaction
from app.models import WatchedAuthor, WatchedTag, WebtoonRecord

STATUS_ACTIVE = "active"
STATUS_UNSUBSCRIBED = "unsubscribed"
STATUS_EXCLUDED = "excluded"

SOURCE_MANUAL = "manual"
SOURCE_ARTIST = "artist"
SOURCE_TAG = "tag"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── webtoons ────────────────────────────────────────────────────────

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
        genres=json.loads(row["genres"] or "[]"),
        tags=json.loads(row["tags"] or "[]"),
        latest_episode_no=row["latest_episode_no"],
        is_paused=bool(row["is_paused"]),
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


def update_latest_episode_no(title_id: str, episode_no: int) -> None:
    """네이버에서 확인한 최신 무료회차 no (다운로드 여부와 무관, '새 에피소드' 배지 판정용)."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET latest_episode_no = ?, updated_at = ? WHERE title_id = ?",
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


def update_genres_and_tags(title_id: str, genres: list[str], tags: list[str]) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET genres = ?, tags = ?, updated_at = ? WHERE title_id = ?",
            (json.dumps(genres), json.dumps(tags), _now(), title_id),
        )


def update_is_paused(title_id: str, is_paused: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET is_paused = ?, updated_at = ? WHERE title_id = ?",
            (int(is_paused), _now(), title_id),
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


# ── settings (key-value) ──────────────────────────────────────────

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


def list_all_settings() -> dict[str, str]:
    rows = get_connection().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ── watched_authors (작가 자동추가 레지스트리) ─────────────────────

def _author_row_to_record(row) -> WatchedAuthor:
    return WatchedAuthor(author_id=row["author_id"], author_name=row["author_name"], enabled=bool(row["enabled"]))


def list_watched_authors() -> list[WatchedAuthor]:
    rows = get_connection().execute("SELECT * FROM watched_authors ORDER BY author_name").fetchall()
    return [_author_row_to_record(r) for r in rows]


def upsert_watched_author(author_id: str, author_name: str, enabled: bool) -> None:
    """이미 있으면 이름만 최신화(있으면)하고 enabled는 건드리지 않는다 — 사용자가 끈 걸 자동으로 되돌리지 않기 위해."""
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watched_authors WHERE author_id = ?", (author_id,)
        ).fetchone()
        if existing:
            if author_name:
                conn.execute(
                    "UPDATE watched_authors SET author_name = ?, updated_at = ? WHERE author_id = ?",
                    (author_name, now, author_id),
                )
        else:
            conn.execute(
                "INSERT INTO watched_authors (author_id, author_name, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (author_id, author_name, int(enabled), now, now),
            )


def set_watched_author_enabled(author_id: str, enabled: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE watched_authors SET enabled = ?, updated_at = ? WHERE author_id = ?",
            (int(enabled), _now(), author_id),
        )


def get_enabled_author_ids() -> set[str]:
    rows = get_connection().execute(
        "SELECT author_id FROM watched_authors WHERE enabled = 1"
    ).fetchall()
    return {r["author_id"] for r in rows}


# ── watched_tags (태그 자동추가 레지스트리) ────────────────────────

def _tag_row_to_record(row) -> WatchedTag:
    return WatchedTag(tag_id=row["tag_id"], tag_name=row["tag_name"], enabled=bool(row["enabled"]))


def list_watched_tags() -> list[WatchedTag]:
    rows = get_connection().execute("SELECT * FROM watched_tags ORDER BY tag_name").fetchall()
    return [_tag_row_to_record(r) for r in rows]


def upsert_watched_tag(tag_id: str, tag_name: str, enabled: bool = True) -> None:
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute("SELECT 1 FROM watched_tags WHERE tag_id = ?", (tag_id,)).fetchone()
        if existing:
            if tag_name:
                conn.execute(
                    "UPDATE watched_tags SET tag_name = ?, updated_at = ? WHERE tag_id = ?",
                    (tag_name, now, tag_id),
                )
        else:
            conn.execute(
                "INSERT INTO watched_tags (tag_id, tag_name, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (tag_id, tag_name, int(enabled), now, now),
            )


def set_watched_tag_enabled(tag_id: str, enabled: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE watched_tags SET enabled = ?, updated_at = ? WHERE tag_id = ?",
            (int(enabled), _now(), tag_id),
        )


def delete_watched_tag(tag_id: str) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM watched_tags WHERE tag_id = ?", (tag_id,))


def get_enabled_tag_ids() -> list[str]:
    rows = get_connection().execute(
        "SELECT tag_id FROM watched_tags WHERE enabled = 1"
    ).fetchall()
    return [r["tag_id"] for r in rows]


# ── 백업/복원 ───────────────────────────────────────────────────────

_SECRET_SETTING_KEYS = {"discord_webhook_url", "discord_bot_token", "discord_notify_channel_id"}


def export_all() -> dict:
    """백업에는 디스코드 비밀값을 포함하지 않는다 — 암호화 키가 없는 다른 환경으로
    복원하면 어차피 복호화가 안 되고, 백업 파일 자체가 새어나갈 경우의 위험도 줄인다.
    새 환경에서는 설정 페이지에서 다시 입력하면 된다."""
    conn = get_connection()
    settings_rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM settings").fetchall()
        if r["key"] not in _SECRET_SETTING_KEYS
    ]
    return {
        "webtoons": [dict(r) for r in conn.execute("SELECT * FROM webtoons").fetchall()],
        "settings": settings_rows,
        "watched_authors": [dict(r) for r in conn.execute("SELECT * FROM watched_authors").fetchall()],
        "watched_tags": [dict(r) for r in conn.execute("SELECT * FROM watched_tags").fetchall()],
    }


def restore_all(data: dict) -> None:
    """백업 데이터로 4개 테이블을 완전히 교체한다 (기존 내용은 전부 지워짐)."""
    with write_transaction() as conn:
        for table in ("webtoons", "settings", "watched_authors", "watched_tags"):
            conn.execute(f"DELETE FROM {table}")

        for row in data.get("webtoons", []):
            columns = list(row.keys())
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO webtoons ({', '.join(columns)}) VALUES ({placeholders})",
                [row[c] for c in columns],
            )
        for row in data.get("settings", []):
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (row["key"], row["value"]))
        for row in data.get("watched_authors", []):
            columns = list(row.keys())
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO watched_authors ({', '.join(columns)}) VALUES ({placeholders})",
                [row[c] for c in columns],
            )
        for row in data.get("watched_tags", []):
            columns = list(row.keys())
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO watched_tags ({', '.join(columns)}) VALUES ({placeholders})",
                [row[c] for c in columns],
            )
