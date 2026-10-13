"""네이버 웹툰 구독 기록(webtoons 테이블) — 조회, 등록, 상태, 회차/작가/장르 정보 갱신, 완결 처리.
"""

import json

from app.db import fetchall, fetchone, write_transaction
from app.models import WebtoonRecord

from app.repository.common import (
    SOURCE_ARTIST,
    SOURCE_MANUAL,
    SOURCE_TAG,
    STATUS_ACTIVE,
    STATUS_EXCLUDED,
    STATUS_UNSUBSCRIBED,
    _now,
)

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
        new_episode_seen_no=row["new_episode_seen_no"],
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


def exclude_auto_added_titles(title_ids: set[str]) -> list[tuple[str, str]]:
    """title_ids(예: 네이버 매일+ 작품) 중 신작 스캔(작가/태그)이 자동으로 추가했고 아직 구독 중인 작품을 "제외됨"으로 바꿔서 (title_id, 제목) 목록을 돌려준다.
    다운로드 대상에서 빠지고, 제외됨은 이미 있는 것으로 취급돼서 신작 스캔이 다시 추가하지 않는다. 사용자가 직접 구독한 작품과 구독해제/제외됨은 건드리지 않는다."""
    if not title_ids:
        return []
    with write_transaction() as conn:
        rows = conn.execute(
            "SELECT title_id, title FROM webtoons WHERE status = ? AND added_source IN (?, ?)", (STATUS_ACTIVE, SOURCE_ARTIST, SOURCE_TAG)
        ).fetchall()
        targets = [(row["title_id"], row["title"]) for row in rows if row["title_id"] in title_ids]
        for title_id, _ in targets:
            conn.execute("UPDATE webtoons SET status = ?, updated_at = ? WHERE title_id = ?", (STATUS_EXCLUDED, _now(), title_id))
    return targets


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


def set_naver_new_episode_seen(title_id: str, episode_no: int) -> None:
    """구독해제 웹툰의 새 에피소드 알림 기준(마지막으로 확인한 최신 회차 번호)을 기록한다."""
    with write_transaction() as conn:
        conn.execute("UPDATE webtoons SET new_episode_seen_no = ?, updated_at = ? WHERE title_id = ?", (episode_no, _now(), title_id))


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
