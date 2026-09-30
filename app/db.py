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
    writer_names TEXT NOT NULL DEFAULT '[]',      -- JSON 배열 (writer_ids와 같은 순서로 대응)
    origin_ids TEXT NOT NULL DEFAULT '[]',        -- JSON 배열: 원작자 id(원작자가 있는 작품만)
    origin_names TEXT NOT NULL DEFAULT '[]',      -- JSON 배열 (origin_ids와 같은 순서로 대응)
    added_source TEXT NOT NULL DEFAULT 'manual',  -- manual | artist | tag
    last_downloaded_no INTEGER NOT NULL DEFAULT 0,
    is_finished INTEGER NOT NULL DEFAULT 0,
    finish_ack INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    finish_notified INTEGER NOT NULL DEFAULT 0,
    genres TEXT NOT NULL DEFAULT '[]',            -- JSON 배열
    tags TEXT NOT NULL DEFAULT '[]',              -- JSON 배열
    latest_episode_no INTEGER NOT NULL DEFAULT 0, -- 마지막으로 확인한 네이버 최신 무료회차 no
    is_paused INTEGER NOT NULL DEFAULT 0,         -- 휴재 여부
    is_new INTEGER NOT NULL DEFAULT 0,            -- 신작 여부 (네이버 API의 'new' 필드)
    has_update INTEGER NOT NULL DEFAULT 0,        -- UP 여부 (네이버 API의 'up' 필드, 탭과 무관하게 그대로 표시)
    ever_subscribed INTEGER NOT NULL DEFAULT 0,   -- 실제로 "구독"을 거친 적이 있는지 (화면 표시용, 상태 전환 로직에는 안 쓰임)
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

CREATE TABLE IF NOT EXISTS kakao_seen_titles (
    author_name TEXT NOT NULL,
    title_id INTEGER NOT NULL,
    title_name TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL,
    PRIMARY KEY (author_name, title_id)
);

-- "웹툰 전체목록"/"구독해제"/"제외됨" 탭에서 카카오웹툰을 다룰 때 쓴다. 네이버
-- title_id와 카카오 title_id는 서로 독립된 숫자 체계라 같은 값이 우연히 겹칠 수
-- 있어서, webtoons 테이블(네이버 전용)과는 절대 안 섞고 이 전용 테이블로 완전히
-- 분리해둔다. status/ever_subscribed는 webtoons 테이블과 같은 규칙을 그대로
-- 따른다(active/unsubscribed/excluded/unregistered, ever_subscribed는 active로
-- 전환되는 순간만 세워지고 이후 절대 안 풀림) — 다만 이 상태 자체는 "웹툰 뷰어
-- 서버 주소"가 설정돼 있어야만 의미가 있다(구독=뷰어로 보고 싶은 것으로 표시,
-- 다운로드를 뜻하는 게 아님). 설정 안 해두면 구독 개념 없이 이 테이블은 제외
-- 기록(status='excluded')만 쓰인다.
CREATE TABLE IF NOT EXISTS kakao_webtoons (
    title_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'excluded',
    ever_subscribed INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    author_summary TEXT NOT NULL DEFAULT '',
    writer_names TEXT NOT NULL DEFAULT '[]',      -- JSON 배열: 작품 정보의 "글" 작가(파일명 템플릿의 {author}에 쓴다)
    is_finished INTEGER NOT NULL DEFAULT 0,      -- 완결이고 받을 회차를 다 받음(완결 확인 알림 대상)
    finish_notified INTEGER NOT NULL DEFAULT 0,  -- 완결 확인 디스코드 메시지를 보냄
    finish_ack INTEGER NOT NULL DEFAULT 0,       -- "알람 제외"를 누름(구독은 유지)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_targets (
    title_id TEXT PRIMARY KEY,   -- 웹툰 대상: 실제 title_id / 폴더 대상: "folder_"로 시작하는 합성 id
    dest_base_path TEXT NOT NULL,
    dest_type TEXT NOT NULL DEFAULT 'local',
    enabled INTEGER NOT NULL DEFAULT 1,
    source_type TEXT NOT NULL DEFAULT 'webtoon',  -- webtoon | folder
    source_dest_type TEXT NOT NULL DEFAULT 'local',  -- folder 대상의 원본 위치: local | rclone
    source_path TEXT NOT NULL DEFAULT '',            -- folder 대상의 원본 경로
    display_name TEXT NOT NULL DEFAULT '',           -- folder 대상의 표시 이름 (비우면 원본 폴더명 사용)
    filename_template_preset_id INTEGER,             -- NULL이면 "기본(전역)" 프리셋 사용
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filename_template_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL,
    title_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    trigger_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archive_history_archived_at ON archive_history(archived_at DESC);

CREATE TABLE IF NOT EXISTS archive_pending_finish (
    title_id TEXT PRIMARY KEY,
    marked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_tags (
    tag_id TEXT PRIMARY KEY,
    tag_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,       -- discovery | download | manual | registry
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,         -- success | error
    log TEXT NOT NULL DEFAULT '[]'  -- JSON 배열 (그 실행의 로그 라인들)
);
CREATE INDEX IF NOT EXISTS idx_job_history_job_name ON job_history(job_name, started_at DESC);

CREATE TABLE IF NOT EXISTS episode_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL,
    title_name TEXT NOT NULL,
    episode_no INTEGER NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,            -- success | failed
    error_msg TEXT NOT NULL DEFAULT '',
    downloaded_at TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'naver'   -- naver | kakao
);
CREATE INDEX IF NOT EXISTS idx_episode_history_title ON episode_history(title_id, downloaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_episode_history_status ON episode_history(status, downloaded_at DESC);
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
    ("webtoons", "is_new", "ALTER TABLE webtoons ADD COLUMN is_new INTEGER NOT NULL DEFAULT 0"),
    ("webtoons", "origin_ids", "ALTER TABLE webtoons ADD COLUMN origin_ids TEXT NOT NULL DEFAULT '[]'"),
    ("webtoons", "origin_names", "ALTER TABLE webtoons ADD COLUMN origin_names TEXT NOT NULL DEFAULT '[]'"),
    ("webtoons", "has_update", "ALTER TABLE webtoons ADD COLUMN has_update INTEGER NOT NULL DEFAULT 0"),
    ("webtoons", "writer_names", "ALTER TABLE webtoons ADD COLUMN writer_names TEXT NOT NULL DEFAULT '[]'"),
    ("webtoons", "ever_subscribed", "ALTER TABLE webtoons ADD COLUMN ever_subscribed INTEGER NOT NULL DEFAULT 0"),
    ("watched_authors", "platform", "ALTER TABLE watched_authors ADD COLUMN platform TEXT NOT NULL DEFAULT 'naver'"),
    ("archive_targets", "dest_type", "ALTER TABLE archive_targets ADD COLUMN dest_type TEXT NOT NULL DEFAULT 'local'"),
    ("archive_targets", "source_type", "ALTER TABLE archive_targets ADD COLUMN source_type TEXT NOT NULL DEFAULT 'webtoon'"),
    ("archive_targets", "source_dest_type", "ALTER TABLE archive_targets ADD COLUMN source_dest_type TEXT NOT NULL DEFAULT 'local'"),
    ("archive_targets", "source_path", "ALTER TABLE archive_targets ADD COLUMN source_path TEXT NOT NULL DEFAULT ''"),
    ("kakao_webtoons", "author_summary", "ALTER TABLE kakao_webtoons ADD COLUMN author_summary TEXT NOT NULL DEFAULT ''"),
    ("kakao_webtoons", "writer_names", "ALTER TABLE kakao_webtoons ADD COLUMN writer_names TEXT NOT NULL DEFAULT '[]'"),
    ("kakao_webtoons", "is_finished", "ALTER TABLE kakao_webtoons ADD COLUMN is_finished INTEGER NOT NULL DEFAULT 0"),
    ("kakao_webtoons", "finish_notified", "ALTER TABLE kakao_webtoons ADD COLUMN finish_notified INTEGER NOT NULL DEFAULT 0"),
    ("kakao_webtoons", "finish_ack", "ALTER TABLE kakao_webtoons ADD COLUMN finish_ack INTEGER NOT NULL DEFAULT 0"),
    ("episode_history", "platform", "ALTER TABLE episode_history ADD COLUMN platform TEXT NOT NULL DEFAULT 'naver'"),
    ("archive_targets", "display_name", "ALTER TABLE archive_targets ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"),
    ("archive_targets", "filename_template_preset_id", "ALTER TABLE archive_targets ADD COLUMN filename_template_preset_id INTEGER"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, alter_sql in _MIGRATIONS:
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_columns:
            conn.execute(alter_sql)
            if table == "webtoons" and column == "ever_subscribed":
                # 이 컬럼이 새로 생기는 바로 이 순간에만, 지금 이미 구독중인 것들은
                # 당연히 구독을 거친 것이므로 한 번만 채워준다 — 이후로는 set_status가
                # active로 바뀔 때마다 알아서 채우므로 이 백필은 다시 필요 없다.
                conn.execute("UPDATE webtoons SET ever_subscribed = 1 WHERE status = 'active'")
    conn.commit()


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(settings.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# 카카오웹툰 → 카카오페이지 통합으로 작품 번호 체계가 바뀌었다. 옛 카카오웹툰 번호는 4자리
# 안팎, 카카오페이지 series_id는 8자리라 이 값 미만이면 옛 카카오웹툰 번호로 본다.
LEGACY_KAKAO_ID_LIMIT = 1_000_000

_connection: sqlite3.Connection | None = None


def _fix_episode_history_platforms(conn: sqlite3.Connection) -> int:
    """이력에 platform 열이 생기기 전에 기록된 카카오페이지 다운로드 이력은 기본값 'naver'로 남는다. 카카오페이지 작품 번호는
    8자리 이상이고 네이버 웹툰 번호는 그보다 훨씬 짧으므로, 8자리 이상이면서 네이버 웹툰 목록에 없는 'naver' 이력을 'kakao'로
    바로잡는다. 이미 맞으면 아무 일도 안 해서 시작할 때마다 실행해도 안전하다. 바로잡은 행 수를 돌려준다."""
    cursor = conn.execute(
        "UPDATE episode_history SET platform = 'kakao' "
        "WHERE platform = 'naver' AND LENGTH(title_id) >= 8 AND title_id NOT IN (SELECT title_id FROM webtoons)"
    )
    return cursor.rowcount


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = _connect()
        _connection.executescript(_SCHEMA)
        _connection.commit()
        _apply_migrations(_connection)
        # 지금 구독 중(active)인데 ever_subscribed가 안 세워진 행이 있으면 항상
        # 바로잡는다 — 예전에 자동추가(작가/태그 감지) 경로가 이 값을 안 세우던
        # 버그가 있었어서, 그 버그가 있던 동안 만들어진 뒤 지금까지 계속 구독 중인
        # 행들을 시작할 때마다 값싸게 자가 치유한다(이미 맞으면 아무 일도 안 함).
        _connection.execute("UPDATE webtoons SET ever_subscribed = 1 WHERE status = 'active' AND ever_subscribed = 0")
        # 관심 작가의 "이미 본 작품" 기준선 중 옛 카카오웹툰 번호로 저장된 것은 이제 아무 데도
        # 안 맞는다 — 그대로 두면 카카오페이지로 첫 스캔할 때 그 작가의 모든 작품이 "신작"으로
        # 알림 폭탄이 된다. 지워두면 작가별 다음 스캔이 "첫 스캔"으로 취급돼 조용히 기준선만
        # 다시 쌓는다(이미 지운 뒤에는 옛 번호 행이 없어서 매번 실행돼도 아무 일도 안 함).
        _connection.execute("DELETE FROM kakao_seen_titles WHERE title_id < ?", (LEGACY_KAKAO_ID_LIMIT,))
        _fix_episode_history_platforms(_connection)
        _connection.commit()
    return _connection


def fix_episode_history_platforms() -> int:
    """(테스트/수동 점검용) 지금 연결에서 이력의 플랫폼 표시를 바로잡는다 — 시작할 때 자동으로도 실행된다."""
    with write_transaction() as conn:
        return _fix_episode_history_platforms(conn)


@contextmanager
def read_lock():
    """읽기 쿼리도 이 락으로 감싸서 실행한다. check_same_thread=False로 크로스스레드
    접근 자체는 허용해뒀지만, 여러 스레드가 "동시에" 같은 sqlite3 Connection 객체를
    건드리면 내부 커서 상태가 꼬여 'bad parameter or other API misuse' 에러가 실제로
    발생했다 — 폴링이 잦은 화면(아카이빙 설정 등)에서 여러 요청이 asyncio.to_thread로
    서로 다른 스레드에서 동시에 조회할 때 재현됨. write_transaction과 같은 락을 공유해서
    모든 DB 접근(읽기+쓰기)을 완전히 직렬화한다 — SQLite는 로컬 파일 기반이라 이 정도
    직렬화로 인한 성능 영향은 미미하다."""
    with _write_lock:
        yield get_connection()


def fetchone(query: str, params: tuple = ()) -> sqlite3.Row | None:
    with read_lock() as conn:
        return conn.execute(query, params).fetchone()


def fetchall(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_lock() as conn:
        return conn.execute(query, params).fetchall()


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
