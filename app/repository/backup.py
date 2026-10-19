"""설정 백업 내보내기/검증/복원.
"""

from datetime import datetime, timezone

from app.db import read_lock, write_transaction

# ── 백업/복원 ───────────────────────────────────────────────────────

# 백업 파일 형식 버전. 새 버전의 백업은 구버전 프로그램이 복원하지 못하게 막고(데이터가 조용히 잘못 들어가는 것보다 낫다),
# 버전이 없는(예전) 백업은 1로 보고 그대로 복원한다.
BACKUP_FORMAT_VERSION = 2


# 백업에 넣지 않는 설정은 세 종류다. 복원할 때의 취급이 서로 다르다.
#  ① 비밀값: 암호화 키가 이 환경의 데이터 볼륨에만 있어서 다른 환경에서는 어차피 못 풀고, 백업 파일이 새어도 안전하도록 뺀다.
#     복원해도 **지금 쓰는 값을 지우지 않고**(같은 컨테이너에서 복원했다고 웹훅/로그인이 날아가면 안 된다), 백업 파일 안에 들어 있어도 무시한다.
#  ② 캐시: 크고 언제든 다시 채워진다(카카오 전체목록). 복원해도 그대로 둔다.
#  ③ 알림 기록: "하루에 한 번만 알림"을 위한 시각들. 복원하면 초기화해서, 환경이 바뀐 뒤에 다시 알릴 수 있게 한다.
_SECRET_SETTING_KEYS = {"discord_webhook_url", "discord_bot_token", "discord_notify_channel_id", "kakao_page_cookies"}


_CACHE_SETTING_KEYS = {"kakao_catalog_snapshot"}


_ALERT_STATE_SETTING_KEYS = {"kakao_page_alert_expired_at", "kakao_page_alert_soon_at", "adult_cookie_expired_notified"}


_NOT_IN_BACKUP_KEYS = _SECRET_SETTING_KEYS | _CACHE_SETTING_KEYS | _ALERT_STATE_SETTING_KEYS


_BACKUP_TABLES = (
    "webtoons", "settings", "watched_authors", "watched_tags", "kakao_seen_titles", "kakao_webtoons",
    "filename_template_presets", "archive_targets", "archive_history", "episode_history", "archive_pending_finish",
)


def export_all() -> dict:
    """백업에는 디스코드 비밀값을 포함하지 않는다 — 암호화 키가 없는 다른 환경으로
    복원하면 어차피 복호화가 안 되고, 백업 파일 자체가 새어나갈 경우의 위험도 줄인다.
    새 환경에서는 설정 페이지에서 다시 입력하면 된다."""
    with read_lock() as conn:
        settings_rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM settings").fetchall()
            if r["key"] not in _NOT_IN_BACKUP_KEYS
        ]
        return {
            "_meta": {"format_version": BACKUP_FORMAT_VERSION, "created_at": datetime.now(timezone.utc).isoformat()},
            "webtoons": [dict(r) for r in conn.execute("SELECT * FROM webtoons").fetchall()],
            "settings": settings_rows,
            "watched_authors": [dict(r) for r in conn.execute("SELECT * FROM watched_authors").fetchall()],
            "watched_tags": [dict(r) for r in conn.execute("SELECT * FROM watched_tags").fetchall()],
            "kakao_seen_titles": [dict(r) for r in conn.execute("SELECT * FROM kakao_seen_titles").fetchall()],
            "kakao_webtoons": [dict(r) for r in conn.execute("SELECT * FROM kakao_webtoons").fetchall()],
            "filename_template_presets": [dict(r) for r in conn.execute("SELECT * FROM filename_template_presets").fetchall()],
            "archive_targets": [dict(r) for r in conn.execute("SELECT * FROM archive_targets").fetchall()],
            "archive_history": [dict(r) for r in conn.execute("SELECT * FROM archive_history").fetchall()],
            "episode_history": [dict(r) for r in conn.execute("SELECT * FROM episode_history").fetchall()],
            "archive_pending_finish": [dict(r) for r in conn.execute("SELECT * FROM archive_pending_finish").fetchall()],
        }


_WEBTOON_COLUMNS = (
    "title_id", "title", "status", "is_adult", "writer_ids", "added_source",
    "last_downloaded_no", "is_finished", "finish_ack", "thumbnail_url",
    "finish_notified", "genres", "tags", "latest_episode_no", "is_paused",
    "writer_names", "ever_subscribed", "is_new", "has_update", "origin_ids", "origin_names", "new_episode_seen_no", "created_at", "updated_at",
)


_WATCHED_AUTHOR_COLUMNS = ("author_id", "author_name", "enabled", "user_disabled", "platform", "created_at", "updated_at")


_WATCHED_TAG_COLUMNS = ("tag_id", "tag_name", "enabled", "created_at", "updated_at")


_KAKAO_SEEN_TITLE_COLUMNS = ("author_name", "title_id", "title_name", "seen_at")


_KAKAO_WEBTOON_COLUMNS = (
    "title_id", "title", "status", "ever_subscribed", "thumbnail_url", "author_summary", "writer_names", "origin_names",
    "is_finished", "finish_notified", "finish_ack", "downloaded_numbers", "pending_numbers", "ticket_notified_no", "caught_up_notified_no", "new_episode_seen_no", "created_at", "updated_at",
)


_FILENAME_TEMPLATE_PRESET_COLUMNS = ("id", "name", "template", "created_at", "updated_at")


_ARCHIVE_TARGET_COLUMNS = (
    "title_id", "dest_base_path", "dest_type", "enabled", "source_type", "source_dest_type",
    "source_path", "display_name", "filename_template_preset_id", "created_at", "updated_at",
)


_ARCHIVE_HISTORY_COLUMNS = ("id", "title_id", "title_name", "file_name", "archived_at", "trigger_type")


_ARCHIVE_PENDING_FINISH_COLUMNS = ("title_id", "marked_at")


_EPISODE_HISTORY_COLUMNS = (
    "id", "title_id", "title_name", "episode_no", "subtitle", "status", "error_msg", "downloaded_at", "platform",
)


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


def validate_backup(data) -> None:
    """복원해도 되는 백업인지 확인한다(지우기 전에). 아니면 ValueError — 데이터는 건드리지 않는다.
    빈 JSON이나 엉뚱한 JSON을 올려도 "복원"이 돼 버리면 현재 데이터가 전부 지워지는 사고가 나므로, 백업 테이블이 하나라도 있어야 한다."""
    if not isinstance(data, dict):
        raise ValueError("백업 데이터 형식이 올바르지 않습니다 (JSON 객체가 아님).")
    if not any(isinstance(data.get(table), list) for table in _BACKUP_TABLES):
        raise ValueError("백업 파일이 아닌 것 같습니다 (백업 데이터가 하나도 들어 있지 않습니다).")
    meta = data.get("_meta")
    version = meta.get("format_version", 1) if isinstance(meta, dict) else 1
    if not isinstance(version, int) or version > BACKUP_FORMAT_VERSION:
        raise ValueError("이 백업은 더 새로운 버전의 프로그램에서 만들어져서 복원할 수 없습니다. 프로그램을 최신 버전으로 업데이트한 뒤 다시 시도해 주세요.")


def restore_all(data: dict) -> None:
    """백업 데이터로 11개 테이블을 완전히 교체한다 (기존 내용은 전부 지워짐).
    설정은 비밀값/캐시(_SECRET/_CACHE_SETTING_KEYS)만 지금 값을 남기고 나머지를 교체하며, 알림 기록은 지워서 초기화한다.
    백업 파일 안의 비밀값/캐시/알림 기록 항목은 무시한다(조작된 백업이 웹훅 주소 같은 걸 바꿔치기하지 못하게)."""
    validate_backup(data)
    keep = sorted(_SECRET_SETTING_KEYS | _CACHE_SETTING_KEYS)

    with write_transaction() as conn:
        for table in _BACKUP_TABLES:
            if table == "settings":
                conn.execute(f"DELETE FROM settings WHERE key NOT IN ({', '.join('?' for _ in keep)})", keep)
            else:
                conn.execute(f"DELETE FROM {table}")

        _insert_validated_rows(conn, "webtoons", _WEBTOON_COLUMNS, data.get("webtoons") or [])
        for row in data.get("settings") or []:
            if isinstance(row, dict) and "key" in row and "value" in row and row["key"] not in _NOT_IN_BACKUP_KEYS:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (row["key"], row["value"]),
                )
        _insert_validated_rows(conn, "watched_authors", _WATCHED_AUTHOR_COLUMNS, data.get("watched_authors") or [])
        _insert_validated_rows(conn, "watched_tags", _WATCHED_TAG_COLUMNS, data.get("watched_tags") or [])
        _insert_validated_rows(conn, "kakao_seen_titles", _KAKAO_SEEN_TITLE_COLUMNS, data.get("kakao_seen_titles") or [])
        _insert_validated_rows(conn, "kakao_webtoons", _KAKAO_WEBTOON_COLUMNS, data.get("kakao_webtoons") or [])
        # 프리셋을 먼저 넣어야 archive_targets.filename_template_preset_id가 참조할 대상이 이미 있다
        # (SQLite가 FK를 강제하진 않지만, id를 그대로 보존해서 순서를 맞춰주는 게 안전하다).
        _insert_validated_rows(
            conn, "filename_template_presets", _FILENAME_TEMPLATE_PRESET_COLUMNS,
            data.get("filename_template_presets") or [],
        )
        _insert_validated_rows(conn, "archive_targets", _ARCHIVE_TARGET_COLUMNS, data.get("archive_targets") or [])
        _insert_validated_rows(conn, "archive_history", _ARCHIVE_HISTORY_COLUMNS, data.get("archive_history") or [])
        _insert_validated_rows(conn, "episode_history", _EPISODE_HISTORY_COLUMNS, data.get("episode_history") or [])
        _insert_validated_rows(
            conn, "archive_pending_finish", _ARCHIVE_PENDING_FINISH_COLUMNS, data.get("archive_pending_finish") or [],
        )
