"""작가/태그 관심 목록(watched_authors, watched_tags).
"""

from app.db import fetchall, write_transaction
from app.models import WatchedAuthor, WatchedTag

from app.repository.common import (
    _now,
)

# ── watched_authors (작가 자동추가 레지스트리) ─────────────────────

def _author_row_to_record(row) -> WatchedAuthor:
    return WatchedAuthor(
        author_id=row["author_id"], author_name=row["author_name"], enabled=bool(row["enabled"]),
        platform=row["platform"],
    )


def list_watched_authors(platform: str = "naver") -> list[WatchedAuthor]:
    rows = fetchall("SELECT * FROM watched_authors WHERE platform = ? ORDER BY author_name", (platform,))
    return [_author_row_to_record(r) for r in rows]


AUTHOR_ADDED = "added"
AUTHOR_ALREADY_ON = "already_on"
AUTHOR_ALREADY_OFF = "already_off"


def upsert_watched_author(author_id: str, author_name: str, enabled: bool, platform: str = "naver") -> str:
    """이미 있으면 이름만 최신화(있으면)하고 enabled는 건드리지 않는다 — 사용자가 끈 걸 자동으로 되돌리지 않기 위해.
    새로 넣었는지(AUTHOR_ADDED), 이미 켜져 있었는지(AUTHOR_ALREADY_ON), 꺼져 있었는지(AUTHOR_ALREADY_OFF)를 돌려준다."""
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute(
            "SELECT enabled FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform)
        ).fetchone()
        if existing:
            if author_name:
                conn.execute(
                    "UPDATE watched_authors SET author_name = ?, updated_at = ? WHERE author_id = ? AND platform = ?",
                    (author_name, now, author_id, platform),
                )
            return AUTHOR_ALREADY_ON if existing["enabled"] else AUTHOR_ALREADY_OFF
        conn.execute(
            "INSERT INTO watched_authors (author_id, author_name, enabled, platform, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (author_id, author_name, int(enabled), platform, now, now),
        )
        return AUTHOR_ADDED


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
    rows = fetchall("SELECT author_id FROM watched_authors WHERE enabled = 1 AND platform = ?", (platform,))
    return {r["author_id"] for r in rows}


def delete_watched_author(author_id: str, platform: str = "naver") -> None:
    """레지스트리에서 완전히 지운다 (이름 없이 남은 예전 찌꺼기 데이터 정리용)."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform))


# ── watched_tags (태그 자동추가 레지스트리) ────────────────────────

def _tag_row_to_record(row) -> WatchedTag:
    return WatchedTag(tag_id=row["tag_id"], tag_name=row["tag_name"], enabled=bool(row["enabled"]))


def list_watched_tags() -> list[WatchedTag]:
    rows = fetchall("SELECT * FROM watched_tags ORDER BY tag_name")
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
    rows = fetchall("SELECT tag_id FROM watched_tags WHERE enabled = 1")
    return [r["tag_id"] for r in rows]
