"""
webtoons/settings/watched_authors/watched_tags 테이블에 대한 유일한 접근 경로.

다른 모듈(tracker/scheduler/api)은 여기 정의된 함수만 호출하고, sqlite3나
SQL을 직접 다루지 않는다 (SRP). 모든 함수는 동기(sync)이며, 비동기 코드에서
호출할 때는 호출부에서 asyncio.to_thread로 감싼다.
"""

import json
from datetime import datetime, timedelta, timezone

from app.db import LEGACY_KAKAO_ID_LIMIT, fetchall, fetchone, read_lock, write_transaction
from app.file_utils import title_key
from app.models import ArchiveTarget, FilenameTemplatePreset, WatchedAuthor, WatchedTag, WebtoonRecord

STATUS_ACTIVE = "active"
STATUS_UNSUBSCRIBED = "unsubscribed"
STATUS_EXCLUDED = "excluded"
STATUS_UNREGISTERED = "unregistered"  # 한 번이라도 구독했던 작품을 "목록으로" 보낼 때만 쓰는 상태.
# 구독해제/제외됨 어느 탭에도 안 뜨고(그 탭들은 unsubscribed/excluded만 조회하므로),
# 네이버 전체목록 보완 로직(구독/제외됨과 무관하게 DB에 남아있으면 계속 보여줌)에서는
# excluded만 걸러내고 이 상태는 안 걸러내므로, 완결/휴재라 요일별 목록엔 없는 작품도
# 전체목록에서 계속 찾을 수 있다 — 구독한 적 없는 작품은 완전 삭제해버리므로 이 상태를
# 아예 거치지 않는다(그런 건 네이버 목록에서 사라지면 같이 사라져도 상관없다고 확인함).

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
        has_update=bool(row["has_update"]),
        ever_subscribed=bool(row["ever_subscribed"]),
    )


def list_all() -> list[WebtoonRecord]:
    rows = fetchall("SELECT * FROM webtoons ORDER BY title")
    return [_row_to_record(r) for r in rows]


def list_by_status(status: str) -> list[WebtoonRecord]:
    rows = fetchall("SELECT * FROM webtoons WHERE status = ? ORDER BY title", (status,))
    return [_row_to_record(r) for r in rows]


def get(title_id: str) -> WebtoonRecord | None:
    row = fetchone("SELECT * FROM webtoons WHERE title_id = ?", (title_id,))
    return _row_to_record(row) if row else None


def exists(title_id: str) -> bool:
    row = fetchone("SELECT 1 FROM webtoons WHERE title_id = ?", (title_id,))
    return row is not None


def upsert_new(
    title_id: str,
    title: str,
    is_adult: bool = False,
    writer_ids: list[str] | None = None,
    added_source: str = SOURCE_MANUAL,
    thumbnail_url: str = "",
    mark_ever_subscribed: bool = True,
) -> None:
    """이미 존재하면 아무 것도 하지 않는다 (구독 취소/제외 상태를 덮어쓰지 않기 위해).

    exists() 체크 후 별도로 INSERT하면 두 코루틴(예: 작가 스캔과 태그 스캔이 동시에
    같은 신작을 발견하는 경우)이 동시에 exists()==False를 보고 둘 다 INSERT를
    시도해서 IntegrityError로 죽을 수 있다(실제로 스레드 두 개로 재현됨) — INSERT OR
    IGNORE로 존재 여부 확인과 삽입을 원자적으로 묶어서 이 레이스 자체를 없앤다.

    이 함수는 항상 status='active'로 새로 만든다 — 대부분의 호출부(수동 구독,
    작가/태그 자동추가)는 이게 진짜 구독 시작이라 ever_subscribed도 같이 1로
    세워야 한다(기본값 True). 유일한 예외는 "미등록 상태에서 바로 제외" 흐름
    (naver_list_exclude)처럼, 이 INSERT가 끝나자마자 바로 다른 상태로 전환해버려서
    실제로는 구독이 아니었던 경우 — 그 호출부만 mark_ever_subscribed=False로
    넘겨서 구독 이력이 잘못 남는 걸 막는다."""
    now = _now()
    with write_transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO webtoons
                (title_id, title, status, is_adult, writer_ids, added_source,
                 last_downloaded_no, is_finished, finish_ack, thumbnail_url, ever_subscribed, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)
            """,
            (
                title_id,
                title,
                STATUS_ACTIVE,
                int(is_adult),
                json.dumps(writer_ids or []),
                added_source,
                thumbnail_url,
                int(mark_ever_subscribed),
                now,
                now,
            ),
        )


def register_as_unsubscribed_history(title_id: str, title: str, thumbnail_url: str = "") -> None:
    """"구독해제 등록"(설정 화면) 전용 — 없으면 새로 만들고, 있으면 그대로 두고,
    구독 중(active)이 아닌 이상 무조건 status=unsubscribed + ever_subscribed=1로
    만든다. 실제 과거 이력과 무관하게 사용자가 직접 이력을 만들어주는 기능이라
    무조건 1로 세운다 — set_status의 CASE 로직(활성화될 때만 세움)과 달리, 여기는
    호출 자체가 "이 작품은 구독 이력이 있는 걸로 쳐줘"라는 명시적 의도이기 때문이다."""
    upsert_new(title_id, title, thumbnail_url=thumbnail_url, mark_ever_subscribed=True)
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET status = ?, ever_subscribed = 1, updated_at = ? WHERE title_id = ?",
            (STATUS_UNSUBSCRIBED, _now(), title_id),
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
    """상태를 바꾼다. active로 바뀌는 순간(구독을 시작하는 순간) ever_subscribed도
    같이 1로 세워두고 이후로는 절대 되돌리지 않는다 — "구독한 적 없이 제외됨→
    목록으로만 왔다갔다 한 것"과 "실제로 구독했다가 해제한 것"을 구분해서, 네이버
    전체목록에서 잘못된 배지("구독해제")가 붙는 걸 막기 위한 용도라, 이 값 자체가
    나중에 상태를 바꾸는 데는 전혀 쓰이지 않고 오직 화면 표시용으로만 쓰인다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET status = ?, ever_subscribed = CASE WHEN ? = ? THEN 1 ELSE ever_subscribed END, "
            "updated_at = ? WHERE title_id = ?",
            (status, status, STATUS_ACTIVE, _now(), title_id),
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


def update_origin_ids_and_names(title_id: str, origin_ids: list[str], origin_names: list[str]) -> None:
    """원작자(있는 작품만)의 id/이름을 저장한다. 작가(writer_*)와 별개로 두는 이유: 카드에 표시하는 작가 정보는
    그대로 두고, 관심 작가 후보 목록에만 원작자를 더하기 위해서다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET origin_ids = ?, origin_names = ?, updated_at = ? WHERE title_id = ?",
            (json.dumps(origin_ids), json.dumps(origin_names), _now(), title_id),
        )


def list_origin_author_ids() -> set[str]:
    """DB의 웹툰 어딘가에서 원작자로 나온 적 있는 작가 id들(화면에서 "원작"으로 표시하려는 용도)."""
    ids: set[str] = set()
    for row in fetchall("SELECT origin_ids FROM webtoons"):
        ids.update(json.loads(row["origin_ids"] or "[]"))
    return ids


def list_all_writer_id_name_pairs() -> dict[str, str]:
    """상태와 무관하게 DB에 있는 모든 웹툰에서 (author_id -> author_name)을 모은다(작가와 원작자 모두).
    watched_authors에 이름 없이 등록된 경우 이걸로 보정하고, 관심 작가 후보 목록도 이걸로 채운다."""
    rows = fetchall("SELECT writer_ids, writer_names, origin_ids, origin_names FROM webtoons")
    result: dict[str, str] = {}
    for row in rows:
        for ids_key, names_key in (("writer_ids", "writer_names"), ("origin_ids", "origin_names")):
            ids = json.loads(row[ids_key] or "[]")
            names = json.loads(row[names_key] or "[]")
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


def update_has_update(title_id: str, has_update: bool) -> None:
    """네이버 API의 'up'(새 회차 업데이트) 값을 그대로 저장한다. 개별 작품 상세
    API에는 이 값이 없고 '요일별 전체목록' 조회 시에만 얻을 수 있어서, 그 목록을
    훑을 때(브라우징 API)만 이 값이 갱신된다 — 탭마다 다르게 계산하지 않고,
    항상 이 저장된 값을 그대로 보여주기 위함."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE webtoons SET has_update = ?, updated_at = ? WHERE title_id = ?",
            (int(has_update), _now(), title_id),
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
    row = fetchone("SELECT value FROM settings WHERE key = ?", (key,))
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
    rows = fetchall("SELECT * FROM watched_authors WHERE platform = ? ORDER BY author_name", (platform,))
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
    rows = fetchall("SELECT author_id FROM watched_authors WHERE enabled = 1 AND platform = ?", (platform,))
    return {r["author_id"] for r in rows}



def delete_watched_author(author_id: str, platform: str = "naver") -> None:
    """레지스트리에서 완전히 지운다 (이름 없이 남은 예전 찌꺼기 데이터 정리용)."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM watched_authors WHERE author_id = ? AND platform = ?", (author_id, platform))


# ── kakao_seen_titles (카카오웹툰 작가별로 이미 알고 있는 작품 목록) ──────

def get_seen_kakao_title_ids(author_name: str) -> set[int]:
    rows = fetchall("SELECT title_id FROM kakao_seen_titles WHERE author_name = ?", (author_name,))
    return {r["title_id"] for r in rows}


def add_seen_kakao_title(author_name: str, title_id: int, title_name: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kakao_seen_titles (author_name, title_id, title_name, seen_at) VALUES (?, ?, ?, ?)",
            (author_name, title_id, title_name, _now()),
        )


# ── kakao_webtoons ("웹툰 전체목록"/"구독해제"/"제외됨" 탭의 카카오웹툰 상태) ──
# webtoons 테이블(네이버)과 같은 상태값(active/unsubscribed/excluded/unregistered)과
# ever_subscribed 규칙을 그대로 쓰지만, title_id 충돌을 피하려고 완전히 별도
# 테이블로 둔다. "구독"은 다운로드를 뜻하지 않는다 — 웹툰 뷰어 서버 주소가
# 설정돼 있을 때만 의미가 있고(뷰어로 계속 보고 싶다는 표시), 그게 없으면
# status='excluded'(목록제외)만 쓰인다.

def _row_to_kakao_webtoon(row) -> dict:
    return {
        "title_id": row["title_id"], "title": row["title"], "status": row["status"],
        "ever_subscribed": bool(row["ever_subscribed"]), "thumbnail_url": row["thumbnail_url"],
        "author_summary": row["author_summary"], "writer_names": json.loads(row["writer_names"] or "[]"), "origin_names": json.loads(row["origin_names"] or "[]"),
        "is_finished": bool(row["is_finished"]),
        "finish_notified": bool(row["finish_notified"]), "finish_ack": bool(row["finish_ack"]),
        "ticket_notified_no": row["ticket_notified_no"],
    }


def set_kakao_authors(title_id: int, writer_names: list[str], origin_names: list[str]) -> None:
    """작품 정보의 "글" 작가(파일명 템플릿 {author}용)와 "원작" 작가(관심 작가 화면의 "(원작)" 표시용)를 저장한다. 추적 중인 작품일 때만
    저장하고, 정보를 못 받아 글/원작이 모두 비어 있으면 기존 값을 지우지 않는다(하나라도 있으면 둘 다 그 내용으로 갱신 — 원작자가 없어진 것도
    반영된다)."""
    if not writer_names and not origin_names:
        return
    with write_transaction() as conn:
        conn.execute(
            "UPDATE kakao_webtoons SET writer_names = ?, origin_names = ? WHERE title_id = ?",
            (json.dumps(writer_names, ensure_ascii=False), json.dumps(origin_names, ensure_ascii=False), title_id),
        )


def get_kakao_downloaded_numbers(title_id: int) -> set[int] | None:
    """이 앱이 받았거나 폴더에서 확인한 회차 번호의 "받은 회차 기록". 아카이빙으로 파일이 폴더에서 옮겨져도 남는다. 기록이 아직 없으면
    None(빈 기록 set()과 구분한다 — 없으면 예전 폴더 규칙을, 있으면 기록을 쓴다). 추적하지 않는 작품도 None."""
    row = fetchone("SELECT downloaded_numbers FROM kakao_webtoons WHERE title_id = ?", (title_id,))
    if row is None or row["downloaded_numbers"] is None:
        return None
    return {int(n) for n in json.loads(row["downloaded_numbers"])}


def set_kakao_downloaded_numbers(title_id: int, numbers) -> None:
    """받은 회차 기록을 통째로 바꾼다(빈 기록도 "기록 있음"). 추적하지 않는 작품이면 아무것도 하지 않는다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE kakao_webtoons SET downloaded_numbers = ? WHERE title_id = ?",
            (json.dumps(sorted({int(n) for n in numbers})), title_id),
        )


def add_kakao_downloaded_numbers(title_id: int, numbers) -> None:
    """받은 회차 기록에 번호를 더한다. **기록이 아직 없는 작품(또는 추적하지 않는 작품)에는 아무것도 하지 않는다** — 한 회차짜리 기록이
    새로 생기면 폴더와 아카이빙 이력으로 처음 기록을 만드는 일이 막히기 때문이다(첫 자동 실행이 만든다)."""
    with write_transaction() as conn:
        row = conn.execute("SELECT downloaded_numbers FROM kakao_webtoons WHERE title_id = ?", (title_id,)).fetchone()
        if row is None or row["downloaded_numbers"] is None:
            return
        merged = {int(n) for n in json.loads(row["downloaded_numbers"])} | {int(n) for n in numbers}
        conn.execute("UPDATE kakao_webtoons SET downloaded_numbers = ? WHERE title_id = ?", (json.dumps(sorted(merged)), title_id))


def list_kakao_webtoons_without_record() -> list[dict]:
    """받은 회차 기록이 아직 없는 카카오 작품(모든 상태) — 시작할 때 폴더와 아카이빙 이력으로 소급해서 만들 대상."""
    return [{"title_id": r["title_id"], "title": r["title"]} for r in fetchall("SELECT title_id, title FROM kakao_webtoons WHERE downloaded_numbers IS NULL ORDER BY title_id")]


def list_archive_file_names(title_id: str) -> list[str]:
    """이 아카이빙 대상(웹툰은 title_id, 카카오는 "kakao_<번호>")으로 옮긴 파일 이름들 — 이력 한 줄이 파일 하나다."""
    return [r["file_name"] for r in fetchall("SELECT file_name FROM archive_history WHERE title_id = ?", (title_id,))]


def list_kakao_origin_names() -> set[str]:
    """추적 중인 카카오 작품 어딘가에서 원작 작가로 나온 이름들."""
    names: set[str] = set()
    for row in fetchall("SELECT origin_names FROM kakao_webtoons"):
        names.update(json.loads(row["origin_names"] or "[]"))
    return names


def is_author_auto_register_enabled() -> bool:
    """구독(자동 구독 포함) 시 그 작품 작가를 '등록된 작가'(자동 신작추가 대상)로 자동 등록할지 여부. 값이 명시적으로 '0'일 때만
    꺼짐 — 기존 사용자는 값이 아예 없을 테니 켜짐 유지."""
    return get_setting("auto_register_author_on_subscribe") != "0"


def set_kakao_finished(title_id: int, finished: bool) -> None:
    """완결(이고 받을 회차를 다 받은) 상태를 기록한다. 다시 연재로 돌아오면 알림 기록도 초기화해서 다음 완결 때 다시 알린다."""
    with write_transaction() as conn:
        if finished:
            conn.execute("UPDATE kakao_webtoons SET is_finished = 1, updated_at = ? WHERE title_id = ?", (_now(), title_id))
        else:
            conn.execute(
                "UPDATE kakao_webtoons SET is_finished = 0, finish_notified = 0, finish_ack = 0, updated_at = ? "
                "WHERE title_id = ? AND is_finished = 1", (_now(), title_id),
            )


def set_kakao_finish_notified(title_id: int) -> None:
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET finish_notified = 1, updated_at = ? WHERE title_id = ?", (_now(), title_id))


def set_kakao_ticket_notified(title_id: int, number: int) -> None:
    """"대여권 충전이 필요합니다" 알림을 보낸 회차 번호를 기록한다(같은 회차는 다시 알리지 않으려고)."""
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET ticket_notified_no = ?, updated_at = ? WHERE title_id = ?", (number, _now(), title_id))


def acknowledge_kakao_finish(title_id: int) -> None:
    """알람 제외: 구독은 그대로 두고 완결 알림만 그만 받는다."""
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET finish_ack = 1, updated_at = ? WHERE title_id = ?", (_now(), title_id))


def list_kakao_finish_pending() -> list[dict]:
    """완결이고 받을 회차를 다 받았는데 아직 디스코드로 알리지 않은(알람 제외도 안 한) 구독 중인 카카오 작품."""
    rows = fetchall(
        "SELECT * FROM kakao_webtoons WHERE status = ? AND is_finished = 1 AND finish_notified = 0 AND finish_ack = 0", (STATUS_ACTIVE,)
    )
    return [_row_to_kakao_webtoon(r) for r in rows]


def kakao_webtoon_exists(title_id: int) -> bool:
    row = fetchone("SELECT 1 FROM kakao_webtoons WHERE title_id = ?", (title_id,))
    return row is not None


def get_kakao_webtoon(title_id: int) -> dict | None:
    row = fetchone("SELECT * FROM kakao_webtoons WHERE title_id = ?", (title_id,))
    return _row_to_kakao_webtoon(row) if row else None


def list_kakao_webtoons_by_status(status: str) -> list[dict]:
    rows = fetchall("SELECT * FROM kakao_webtoons WHERE status = ?", (status,))
    return [_row_to_kakao_webtoon(r) for r in rows]


def get_kakao_webtoons_map() -> dict[int, dict]:
    """추적 중인 카카오 작품 전부(title_id → 기록). 목록 800여 개를 그릴 때 작품마다 DB를 따로 부르지 않고 한 번에 읽는다."""
    return {r["title_id"]: _row_to_kakao_webtoon(r) for r in fetchall("SELECT * FROM kakao_webtoons")}


def delete_unmatched_legacy_excluded() -> int:
    """카카오페이지로 못 옮긴 옛 기록 중 "제외됨"인 것을 지운다 — 완결/휴재라 지금 목록에 없는 작품이거나 제목이 달라진
    작품인데, 어차피 지금도 연재 중인 작품이면 전체목록에 다시 나타난다. 구독해제/구독중 기록은 이력이라 남긴다."""
    with write_transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM kakao_webtoons WHERE title_id < ? AND status = ?", (LEGACY_KAKAO_ID_LIMIT, STATUS_EXCLUDED)
        )
        return cursor.rowcount


def get_kakao_excluded_title_ids() -> set[int]:
    rows = fetchall("SELECT title_id FROM kakao_webtoons WHERE status = ?", (STATUS_EXCLUDED,))
    return {r["title_id"] for r in rows}


def upsert_new_kakao_webtoon(
    title_id: int, title: str, thumbnail_url: str = "", status: str = STATUS_ACTIVE,
    author_summary: str = "",
) -> None:
    """이미 있으면 아무것도 안 한다(webtoons.upsert_new와 같은 원자적 INSERT OR
    IGNORE 패턴 — 동시에 같은 작품을 두 번 만들려는 레이스를 막는다). status가
    active면 ever_subscribed도 같이 1로 세운다."""
    now = _now()
    with write_transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kakao_webtoons "
            "(title_id, title, status, ever_subscribed, thumbnail_url, author_summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title_id, title, status, int(status == STATUS_ACTIVE), thumbnail_url, author_summary, now, now),
        )


def refresh_kakao_webtoon_author_summary(title_id: int, author_summary: str) -> None:
    """"웹툰 전체목록"(카카오 원본에서 매번 작가 정보를 새로 받음)에서 구독/제외를
    누를 때, 그 시점의 최신 작가 정보로 갱신해둔다 — 이 정보를 안 저장해두면
    "구독해제"/"제외됨" 탭에서는(요일별 목록을 다시 안 훑으므로) 작가가 영영 안
    보인다. author_summary가 비어있으면 아무것도 안 한다(호출부가 최신 정보를
    안 갖고 있을 때, 이미 저장된 값을 빈 값으로 덮어써서 지워버리면 안 되므로)."""
    if not author_summary:
        return
    with write_transaction() as conn:
        conn.execute(
            "UPDATE kakao_webtoons SET author_summary = ?, updated_at = ? WHERE title_id = ?",
            (author_summary, _now(), title_id),
        )


def set_kakao_webtoon_status(title_id: int, status: str) -> None:
    """webtoons.set_status와 완전히 같은 규칙 — active로 바뀌는 순간에만
    ever_subscribed를 1로 세우고, 이후로는 다른 상태로 바뀌어도 절대 안 풀린다."""
    with write_transaction() as conn:
        conn.execute(
            "UPDATE kakao_webtoons SET status = ?, ever_subscribed = CASE WHEN ? = ? THEN 1 ELSE ever_subscribed END, "
            "updated_at = ? WHERE title_id = ?",
            (status, status, STATUS_ACTIVE, _now(), title_id),
        )


def hard_delete_kakao_webtoon(title_id: int) -> None:
    """"목록으로"(구독 이력 없음) — DB 기록 자체를 지운다. 네이버 hard_delete와 같은 역할."""
    with write_transaction() as conn:
        conn.execute("DELETE FROM kakao_webtoons WHERE title_id = ?", (title_id,))


def list_legacy_kakao_titles() -> list[str]:
    """아직 옛 카카오웹툰 번호로 남아있는(=카카오페이지로 못 옮긴) 기록의 제목들."""
    rows = fetchall("SELECT DISTINCT title FROM kakao_webtoons WHERE title_id < ?", (LEGACY_KAKAO_ID_LIMIT,))
    return [r["title"] for r in rows]


def migrate_legacy_kakao_webtoons(catalog_items: list[dict], candidates: dict[str, list[dict]] | None = None) -> dict:
    """옛 카카오웹툰 기록(번호 체계가 달라 카카오페이지와 안 맞음)을 카카오페이지 작품으로 옮긴다 — 제목이
    같은 작품(공백/문장부호 차이는 무시)이 정확히 하나만 있을 때 번호를 새 series_id로 바꾸고(상태/구독
    이력/생성 시각은 그대로), 그 외에는 건드리지 않고 이유와 후보를 붙여 목록으로 돌려준다."""
    by_key: dict[str, list[dict]] = {}
    for item in catalog_items:
        by_key.setdefault(title_key(item["title_name"]), []).append(item)

    migrated = 0
    unmatched: list[dict] = []
    with write_transaction() as conn:
        legacy_rows = conn.execute(
            "SELECT title_id, title, status FROM kakao_webtoons WHERE title_id < ?", (LEGACY_KAKAO_ID_LIMIT,)
        ).fetchall()
        for row in legacy_rows:
            matches = by_key.get(title_key(row["title"]), []) if title_key(row["title"]) else []
            item = matches[0] if len(matches) == 1 else None
            taken = item is not None and conn.execute(
                "SELECT 1 FROM kakao_webtoons WHERE title_id = ?", (item["title_id"],)
            ).fetchone()
            if item is None or taken:
                if len(matches) > 1:
                    reason = "같은 제목의 작품이 여러 개라 어느 것인지 정할 수 없음"
                    shown = [{"title_id": m["title_id"], "title_name": m["title_name"]} for m in matches[:3]]
                elif taken:
                    reason = "이미 다른 기록이 그 작품으로 등록돼 있음"
                    shown = [{"title_id": item["title_id"], "title_name": item["title_name"]}]
                else:
                    reason = "카카오페이지에서 같은 제목의 작품을 못 찾음"
                    shown = (candidates or {}).get(row["title"], [])
                unmatched.append({"title": row["title"], "status": row["status"], "reason": reason, "candidates": shown})
                continue
            conn.execute(
                "UPDATE kakao_webtoons SET title_id = ?, thumbnail_url = ?, author_summary = ?, updated_at = ? "
                "WHERE title_id = ?",
                (item["title_id"], item["thumbnail_url"], ", ".join(item["author_names"]), _now(), row["title_id"]),
            )
            migrated += 1
    return {"migrated": migrated, "unmatched": unmatched}


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
    "writer_names", "ever_subscribed", "is_new", "has_update", "origin_ids", "origin_names", "created_at", "updated_at",
)
_WATCHED_AUTHOR_COLUMNS = ("author_id", "author_name", "enabled", "platform", "created_at", "updated_at")
_WATCHED_TAG_COLUMNS = ("tag_id", "tag_name", "enabled", "created_at", "updated_at")
_KAKAO_SEEN_TITLE_COLUMNS = ("author_name", "title_id", "title_name", "seen_at")
_KAKAO_WEBTOON_COLUMNS = (
    "title_id", "title", "status", "ever_subscribed", "thumbnail_url", "author_summary", "writer_names", "origin_names",
    "is_finished", "finish_notified", "finish_ack", "downloaded_numbers", "ticket_notified_no", "created_at", "updated_at",
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
