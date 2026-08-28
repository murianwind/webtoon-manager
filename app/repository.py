"""
webtoons/settings/watched_authors/watched_tags 테이블에 대한 유일한 접근 경로.

다른 모듈(tracker/scheduler/api)은 여기 정의된 함수만 호출하고, sqlite3나
SQL을 직접 다루지 않는다 (SRP). 모든 함수는 동기(sync)이며, 비동기 코드에서
호출할 때는 호출부에서 asyncio.to_thread로 감싼다.
"""

import json
from datetime import datetime, timedelta, timezone

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
        writer_names=json.loads(row["writer_names"] or "[]"),
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
        is_new=bool(row["is_new"]),
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
    """이미 존재하면 아무 것도 하지 않는다 (구독 취소/제외 상태를 덮어쓰지 않기 위해).

    exists() 체크 후 별도로 INSERT하면 두 코루틴(예: 작가 스캔과 태그 스캔이 동시에
    같은 신작을 발견하는 경우)이 동시에 exists()==False를 보고 둘 다 INSERT를
    시도해서 IntegrityError로 죽을 수 있다(실제로 스레드 두 개로 재현됨) — INSERT OR
    IGNORE로 존재 여부 확인과 삽입을 원자적으로 묶어서 이 레이스 자체를 없앤다."""
    now = _now()
    with write_transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO webtoons
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


def update_writer_ids_and_names(title_id: str, writer_ids: list[str], writer_names: list[str]) -> None:
    """writer_ids[i]와 writer_names[i]가 같은 작가를 가리키도록 순서를 맞춰서 저장한다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET writer_ids = ?, writer_names = ?, updated_at = ? WHERE title_id = ?",
            (json.dumps(writer_ids), json.dumps(writer_names), _now(), title_id),
        )


def list_all_writer_id_name_pairs() -> dict[str, str]:
    """상태와 무관하게 DB에 있는 모든 웹툰에서 (author_id -> author_name)을 모은다.
    watched_authors에 이름 없이 등록된 경우 이걸로 보정한다."""
    rows = get_connection().execute("SELECT writer_ids, writer_names FROM webtoons").fetchall()
    result: dict[str, str] = {}
    for row in rows:
        ids = json.loads(row["writer_ids"] or "[]")
        names = json.loads(row["writer_names"] or "[]")
        for i, author_id in enumerate(ids):
            name = names[i] if i < len(names) else ""
            if name and not result.get(author_id):
                result[author_id] = name
    return result


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


def update_is_new(title_id: str, is_new: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET is_new = ?, updated_at = ? WHERE title_id = ?",
            (int(is_new), _now(), title_id),
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


# ── watched_authors (작가 자동추가 레지스트리) ─────────────────────

def _author_row_to_record(row) -> WatchedAuthor:
    return WatchedAuthor(
        author_id=row["author_id"], author_name=row["author_name"], enabled=bool(row["enabled"]),
        platform=row["platform"],
    )


def list_watched_authors(platform: str = "naver") -> list[WatchedAuthor]:
    rows = get_connection().execute(
        "SELECT * FROM watched_authors WHERE platform = ? ORDER BY author_name", (platform,)
    ).fetchall()
    return [_author_row_to_record(r) for r in rows]


def upsert_watched_author(author_id: str, author_name: str, enabled: bool, platform: str = "naver") -> None:
    """이미 있으면 이름만 최신화(있으면)하고 enabled는 건드리지 않는다 — 사용자가 끈 걸 자동으로 되돌리지 않기 위해."""
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform)
        ).fetchone()
        if existing:
            if author_name:
                conn.execute(
                    "UPDATE watched_authors SET author_name = ?, updated_at = ? WHERE author_id = ? AND platform = ?",
                    (author_name, now, author_id, platform),
                )
        else:
            conn.execute(
                "INSERT INTO watched_authors (author_id, author_name, enabled, platform, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (author_id, author_name, int(enabled), platform, now, now),
            )


def set_watched_author_enabled(author_id: str, enabled: bool, author_name: str = "", platform: str = "naver") -> None:
    """행이 아직 없으면(구독으로 처음 발견되어 watched_authors에 등록된 적 없는 경우)
    만들어서 저장한다 — UPDATE만 하면 없는 행은 조용히 아무 일도 안 일어나기 때문."""
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE watched_authors SET enabled = ?, updated_at = ? WHERE author_id = ? AND platform = ?",
                (int(enabled), now, author_id, platform),
            )
        else:
            conn.execute(
                "INSERT INTO watched_authors (author_id, author_name, enabled, platform, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (author_id, author_name, int(enabled), platform, now, now),
            )


def get_enabled_author_ids(platform: str = "naver") -> set[str]:
    rows = get_connection().execute(
        "SELECT author_id FROM watched_authors WHERE enabled = 1 AND platform = ?", (platform,)
    ).fetchall()
    return {r["author_id"] for r in rows}



def delete_watched_author(author_id: str, platform: str = "naver") -> None:
    """레지스트리에서 완전히 지운다 (이름 없이 남은 예전 찌꺼기 데이터 정리용)."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform))


# ── kakao_seen_titles (카카오웹툰 작가별로 이미 알고 있는 작품 목록) ──────

def get_seen_kakao_title_ids(author_name: str) -> set[int]:
    rows = get_connection().execute(
        "SELECT title_id FROM kakao_seen_titles WHERE author_name = ?", (author_name,)
    ).fetchall()
    return {r["title_id"] for r in rows}


def add_seen_kakao_title(author_name: str, title_id: int, title_name: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kakao_seen_titles (author_name, title_id, title_name, seen_at) VALUES (?, ?, ?, ?)",
            (author_name, title_id, title_name, _now()),
        )


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
    conn = get_connection()
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
                    "job_name": r["job_name"],
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "status": r["status"],
                    "log": json.loads(r["log"] or "[]"),
                }
            )
    results.sort(key=lambda r: r["started_at"], reverse=True)
    return results


# ── episode_history (회차 단위 다운로드 이력) ──────────────────────

def add_episode_history(
    title_id: str, title_name: str, episode_no: int, subtitle: str, status: str, error_msg: str = ""
) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            INSERT INTO episode_history
                (title_id, title_name, episode_no, subtitle, status, error_msg, downloaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (title_id, title_name, episode_no, subtitle, status, error_msg, _now()),
        )


def list_episode_history(
    status: str | None = None, search: str = "", page: int = 1, page_size: int = 30
) -> tuple[list[dict], int]:
    """(행 목록, 전체 개수)를 반환한다 — 상태/제목 검색 필터 + 페이지네이션."""
    conn = get_connection()
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
    rows = get_connection().execute(
        "SELECT * FROM episode_history WHERE downloaded_at > ? ORDER BY downloaded_at ASC",
        (since_iso,),
    ).fetchall()
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
        }
        for r in rows
    ]



def delete_episode_history_older_than(days: int) -> int:
    """다운로드된 지 N일 넘은 이력을 지운다 (파일은 그대로 유지됨). 지운 개수를 반환."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with write_transaction() as conn:
        cursor = conn.execute("DELETE FROM episode_history WHERE downloaded_at < ?", (cutoff,))
        return cursor.rowcount


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


_WEBTOON_COLUMNS = (
    "title_id", "title", "status", "is_adult", "writer_ids", "added_source",
    "last_downloaded_no", "is_finished", "finish_ack", "thumbnail_url",
    "finish_notified", "genres", "tags", "latest_episode_no", "is_paused",
    "writer_names", "created_at", "updated_at",
)
_WATCHED_AUTHOR_COLUMNS = ("author_id", "author_name", "enabled", "created_at", "updated_at")
_WATCHED_TAG_COLUMNS = ("tag_id", "tag_name", "enabled", "created_at", "updated_at")


def _insert_validated_rows(conn, table: str, allowed_columns: tuple[str, ...], rows: list[dict]) -> None:
    """
    백업 JSON의 키를 SQL 컬럼명으로 그대로 쓰면 조작된 백업 파일로 SQL 인젝션이
    가능해진다(f-string에 row.keys()를 직접 꽂는 형태였음 — 실제로 이런 문제가
    있었다). 그래서 컬럼명은 절대 입력에서 가져오지 않고, 여기 하드코딩된
    allowed_columns 중에서 실제로 그 행에 존재하는 것만, 정해진 순서로만 사용한다.
    모르는 키는 조용히 무시하고(공격 표면이 안 되도록), 필수 컬럼이 하나라도
    없으면 이 행 전체를 건너뛴다(스키마가 다른 백업이어도 크래시 없이 처리).
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        present_columns = [c for c in allowed_columns if c in row]
        if not present_columns:
            continue
        placeholders = ", ".join("?" for _ in present_columns)
        column_list = ", ".join(present_columns)  # allowed_columns에서만 골랐으므로 안전
        conn.execute(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            [row[c] for c in present_columns],
        )


def restore_all(data: dict) -> None:
    """백업 데이터로 4개 테이블을 완전히 교체한다 (기존 내용은 전부 지워짐)."""
    if not isinstance(data, dict):
        raise ValueError("백업 데이터 형식이 올바르지 않습니다 (JSON 객체가 아님).")

    with write_transaction() as conn:
        for table in ("webtoons", "settings", "watched_authors", "watched_tags"):
            conn.execute(f"DELETE FROM {table}")

        _insert_validated_rows(conn, "webtoons", _WEBTOON_COLUMNS, data.get("webtoons") or [])
        for row in data.get("settings") or []:
            if isinstance(row, dict) and "key" in row and "value" in row:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (row["key"], row["value"]))
        _insert_validated_rows(conn, "watched_authors", _WATCHED_AUTHOR_COLUMNS, data.get("watched_authors") or [])
        _insert_validated_rows(conn, "watched_tags", _WATCHED_TAG_COLUMNS, data.get("watched_tags") or [])
