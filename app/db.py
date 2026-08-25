"""
SQLite 연결 관리.

id_list.txt + webtoon_state.json을 대체하는 단일 저장소. WAL 모드로 열어 동시
읽기/쓰기 충돌을 줄이고, 쓰기 자체는 repository.py의 전역 락으로 직렬화한다
(동시성 이슈: 스케줄러 잡과 웹 API가 동시에 같은 파일을 건드릴 수 있으므로).
"""

import sqlite3
import threading
from contextlib import contextmanager

from app.config import get_settings

_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webtoons (
    title_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',       -- active | unsubscribed | excluded
    is_adult INTEGER NOT NULL DEFAULT 0,
    writer_ids TEXT NOT NULL DEFAULT '[]',        -- JSON 배열
    added_source TEXT NOT NULL DEFAULT 'manual',  -- manual | artist | tag
    last_downloaded_no INTEGER NOT NULL DEFAULT 0,
    is_finished INTEGER NOT NULL DEFAULT 0,
    finish_ack INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    finish_notified INTEGER NOT NULL DEFAULT 0,
    genres TEXT NOT NULL DEFAULT '[]',            -- JSON 배열
    tags TEXT NOT NULL DEFAULT '[]',              -- JSON 배열
    latest_episode_no INTEGER NOT NULL DEFAULT 0, -- 마지막으로 확인한 네이버 최신 무료회차 no
    is_paused INTEGER NOT NULL DEFAULT 0,         -- 휴재 여부 (best-effort)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS watched_authors (
    author_id TEXT PRIMARY KEY,
    author_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_tags (
    tag_id TEXT PRIMARY KEY,
    tag_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# 기존에 이미 만들어진 DB(위 스키마에 없던 컬럼이 있던 버전)를 위한 마이그레이션.
# CREATE TABLE IF NOT EXISTS는 이미 있는 테이블의 컬럼을 추가해주지 않기 때문에 별도로 처리한다.
_MIGRATIONS = [
    ("webtoons", "thumbnail_url", "ALTER TABLE webtoons ADD COLUMN thumbnail_url TEXT NOT NULL DEFAULT ''"),
    ("webtoons", "finish_notified", "ALTER TABLE webtoons ADD COLUMN finish_notified INTEGER NOT NULL DEFAULT 0"),
    ("webtoons", "genres", "ALTER TABLE webtoons ADD COLUMN genres TEXT NOT NULL DEFAULT '[]'"),
    ("webtoons", "tags", "ALTER TABLE webtoons ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"),
    ("webtoons", "latest_episode_no", "ALTER TABLE webtoons ADD COLUMN latest_episode_no INTEGER NOT NULL DEFAULT 0"),
    ("webtoons", "is_paused", "ALTER TABLE webtoons ADD COLUMN is_paused INTEGER NOT NULL DEFAULT 0"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, alter_sql in _MIGRATIONS:
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_columns:
            conn.execute(alter_sql)
    conn.commit()


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(settings.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


_connection: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = _connect()
        _connection.executescript(_SCHEMA)
        _connection.commit()
        _apply_migrations(_connection)
    return _connection


@contextmanager
def write_transaction():
    """쓰기 작업은 전부 이 컨텍스트를 통해서만 수행한다 (레이스 컨디션 방지)."""
    with _write_lock:
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
