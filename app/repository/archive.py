import json
"""아카이빙 대상, 파일 이름 틀 프리셋, 아카이빙 이력, 완결 후 대기 목록.
"""

from datetime import datetime, timedelta, timezone

from app.db import fetchall, fetchone, read_lock, write_transaction
from app.models import ArchiveTarget, FilenameTemplatePreset

from app.repository.common import (
    _now,
)
from app.repository.settings import get_setting, set_setting

def list_archive_file_names(title_id: str) -> list[str]:
    """이 아카이빙 대상(웹툰은 title_id, 카카오는 "kakao_<번호>")으로 옮긴 파일 이름들 — 이력 한 줄이 파일 하나다."""
    return [r["file_name"] for r in fetchall("SELECT file_name FROM archive_history WHERE title_id = ?", (title_id,))]


# ── archive_targets (아카이빙 대상 웹툰/폴더 + 목적지 그릇 폴더) ──────────────

def _row_to_archive_target(r) -> ArchiveTarget:
    return ArchiveTarget(
        title_id=r["title_id"], dest_base_path=r["dest_base_path"], enabled=bool(r["enabled"]),
        dest_type=r["dest_type"], source_type=r["source_type"], source_dest_type=r["source_dest_type"],
        source_path=r["source_path"], display_name=r["display_name"],
        filename_template_preset_id=r["filename_template_preset_id"],
    )


def list_archive_targets() -> list[ArchiveTarget]:
    """최근 등록한 게 위로 오도록 정렬한다 — 예전엔 title_id(작품 고유번호) 순이라
    등록 순서와 무관하게 뒤죽박죽으로 보였다."""
    rows = fetchall("SELECT * FROM archive_targets ORDER BY created_at DESC")
    return [_row_to_archive_target(r) for r in rows]


def get_archive_target(title_id: str) -> ArchiveTarget | None:
    row = fetchone("SELECT * FROM archive_targets WHERE title_id = ?", (title_id,))
    if row is None:
        return None
    return _row_to_archive_target(row)


def upsert_archive_target(title_id: str, dest_base_path: str, enabled: bool = True, dest_type: str = "local") -> None:
    """웹툰 대상 등록/수정 — source_type은 항상 'webtoon'."""
    now = _now()
    with write_transaction() as conn:
        existing = conn.execute("SELECT 1 FROM archive_targets WHERE title_id = ?", (title_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE archive_targets SET dest_base_path = ?, dest_type = ?, enabled = ?, updated_at = ? WHERE title_id = ?",
                (dest_base_path, dest_type, int(enabled), now, title_id),
            )
        else:
            conn.execute(
                "INSERT INTO archive_targets (title_id, dest_base_path, dest_type, enabled, source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'webtoon', ?, ?)",
                (title_id, dest_base_path, dest_type, int(enabled), now, now),
            )


def create_folder_archive_target(
    display_name: str, source_dest_type: str, source_path: str, dest_base_path: str, dest_type: str = "local",
    enabled: bool = True,
) -> str:
    """폴더-폴더 대상 등록 (카카오웹툰처럼 웹툰 레코드가 없는 폴더용). 새로 생성한
    합성 id(title_id 자리)를 반환한다."""
    import uuid

    target_id = f"folder_{uuid.uuid4().hex[:12]}"
    now = _now()
    with write_transaction() as conn:
        conn.execute(
            "INSERT INTO archive_targets "
            "(title_id, dest_base_path, dest_type, enabled, source_type, source_dest_type, source_path, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'folder', ?, ?, ?, ?, ?)",
            (target_id, dest_base_path, dest_type, int(enabled), source_dest_type, source_path, display_name, now, now),
        )
    return target_id


def update_folder_archive_target(
    target_id: str, display_name: str, source_dest_type: str, source_path: str, dest_base_path: str, dest_type: str
) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE archive_targets SET display_name = ?, source_dest_type = ?, source_path = ?, "
            "dest_base_path = ?, dest_type = ?, updated_at = ? WHERE title_id = ? AND source_type = 'folder'",
            (display_name, source_dest_type, source_path, dest_base_path, dest_type, _now(), target_id),
        )


def set_archive_target_enabled(title_id: str, enabled: bool) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE archive_targets SET enabled = ?, updated_at = ? WHERE title_id = ?",
            (int(enabled), _now(), title_id),
        )


def set_archive_target_filename_preset(title_id: str, preset_id: int | None) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE archive_targets SET filename_template_preset_id = ?, updated_at = ? WHERE title_id = ?",
            (preset_id, _now(), title_id),
        )


def delete_archive_target(title_id: str) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM archive_targets WHERE title_id = ?", (title_id,))


# ── filename_template_presets (파일명 변경 템플릿 프리셋) ──────────────────

def list_filename_template_presets() -> list[FilenameTemplatePreset]:
    rows = fetchall("SELECT * FROM filename_template_presets ORDER BY created_at ASC")
    return [FilenameTemplatePreset(id=r["id"], name=r["name"], template=r["template"]) for r in rows]


def get_filename_template_preset(preset_id: int) -> FilenameTemplatePreset | None:
    row = fetchone("SELECT * FROM filename_template_presets WHERE id = ?", (preset_id,))
    if row is None:
        return None
    return FilenameTemplatePreset(id=row["id"], name=row["name"], template=row["template"])


def create_filename_template_preset(name: str, template: str) -> int:
    now = _now()
    with write_transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO filename_template_presets (name, template, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, template, now, now),
        )
        return cursor.lastrowid


def update_filename_template_preset(preset_id: int, name: str, template: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "UPDATE filename_template_presets SET name = ?, template = ?, updated_at = ? WHERE id = ?",
            (name, template, _now(), preset_id),
        )


def delete_filename_template_preset(preset_id: int) -> None:
    """이 프리셋을 쓰던 대상들은 삭제 후 자동으로 '기본(전역)'으로 돌아간다(NULL로)."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE archive_targets SET filename_template_preset_id = NULL WHERE filename_template_preset_id = ?",
            (preset_id,),
        )
        conn.execute("DELETE FROM filename_template_presets WHERE id = ?", (preset_id,))


def count_archive_targets_with_base_path(dest_base_path: str, dest_type: str = "local") -> int:
    row = fetchone(
        "SELECT COUNT(*) AS c FROM archive_targets WHERE dest_base_path = ? AND dest_type = ? AND enabled = 1",
        (dest_base_path, dest_type),
    )
    return row["c"]


# ── archive_history (아카이빙 실행 이력) ─────────────────────────────

def add_archive_history(title_id: str, title_name: str, file_name: str, trigger_type: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "INSERT INTO archive_history (title_id, title_name, file_name, archived_at, trigger_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (title_id, title_name, file_name, _now(), trigger_type),
        )


# ── archive_pending_finish (완결 구독해제 이동 대기열 — 다음 아카이빙 주기 때 처리) ──

def add_pending_finish_archive(title_id: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO archive_pending_finish (title_id, marked_at) VALUES (?, ?)",
            (title_id, _now()),
        )


def list_pending_finish_archive() -> list[str]:
    rows = fetchall("SELECT title_id FROM archive_pending_finish")
    return [r["title_id"] for r in rows]


def remove_pending_finish_archive(title_id: str) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM archive_pending_finish WHERE title_id = ?", (title_id,))


def list_archive_history(page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
    with read_lock() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM archive_history").fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM archive_history ORDER BY archived_at DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    items = [
        {
            "id": r["id"],
            "title_id": r["title_id"],
            "title_name": r["title_name"],
            "file_name": r["file_name"],
            "archived_at": r["archived_at"],
            "trigger_type": r["trigger_type"],
        }
        for r in rows
    ]
    return items, total


def clear_archive_history() -> None:
    """이력만 지운다 (실제로 옮겨진 파일은 그대로 유지됨)."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM archive_history")


def delete_archive_history_entry(entry_id: int) -> None:
    with write_transaction() as conn:
        conn.execute("DELETE FROM archive_history WHERE id = ?", (entry_id,))


def delete_archive_history_older_than(days: int) -> int:
    """기록된 지 N일 넘은 아카이빙 이력을 지운다. 지운 개수를 반환."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with write_transaction() as conn:
        cursor = conn.execute("DELETE FROM archive_history WHERE archived_at < ?", (cutoff,))
        return cursor.rowcount


# ── 보관할 폴더 즐겨찾기 — 폴더 선택기에서 자주 쓰는 폴더를 버튼으로 바로 여는 기능(설정 테이블에 JSON으로 저장되어 백업/복원에 함께 들어간다) ──
FOLDER_FAVORITES_KEY = "archive_folder_favorites"
MAX_FOLDER_FAVORITES = 30


def list_folder_favorites() -> list[dict]:
    """등록한 즐겨찾기를 추가한 순서대로. 저장된 값이 깨져 있으면(수동 편집 등) 빈 목록."""
    raw = get_setting(FOLDER_FAVORITES_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [f for f in data if isinstance(f, dict) and f.get("id")] if isinstance(data, list) else []


def _default_favorite_label(dest_type: str, root: str, path: str) -> str:
    name = path.split("/")[-1] if path else "최상위"
    return f"{'로컬' if dest_type == 'local' else root} · {name}"


def add_folder_favorite(dest_type: str, root: str, path: str, label: str = "") -> dict:
    """즐겨찾기를 추가하고 돌려준다. 같은 폴더(종류+루트+경로)가 이미 있으면 새로 만들지 않고 기존 것을 돌려준다. 최대 개수를 넘으면 ValueError."""
    import uuid

    favorites = list_folder_favorites()
    for existing in favorites:
        if (existing.get("dest_type"), existing.get("root"), existing.get("path")) == (dest_type, root, path):
            return existing
    if len(favorites) >= MAX_FOLDER_FAVORITES:
        raise ValueError(f"즐겨찾기는 최대 {MAX_FOLDER_FAVORITES}개까지 등록할 수 있습니다. 안 쓰는 것을 지우고 다시 추가하세요.")
    favorite = {"id": uuid.uuid4().hex[:12], "label": label or _default_favorite_label(dest_type, root, path), "dest_type": dest_type, "root": root, "path": path}
    set_setting(FOLDER_FAVORITES_KEY, json.dumps(favorites + [favorite], ensure_ascii=False))
    return favorite


def delete_folder_favorite(favorite_id: str) -> bool:
    """즐겨찾기 하나를 지운다(나머지 순서 유지). 없는 번호면 False."""
    favorites = list_folder_favorites()
    kept = [f for f in favorites if f.get("id") != favorite_id]
    if len(kept) == len(favorites):
        return False
    set_setting(FOLDER_FAVORITES_KEY, json.dumps(kept, ensure_ascii=False))
    return True
