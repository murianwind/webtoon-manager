"""카카오페이지 작품(kakao_webtoons 테이블)과 본 적 있는 신작 목록, 옛 카카오웹툰 기록 이전.
"""

import json

from app.db import LEGACY_KAKAO_ID_LIMIT, fetchall, fetchone, write_transaction
from app.file_utils import title_key

from app.repository.common import (
    STATUS_ACTIVE,
    STATUS_EXCLUDED,
    _now,
)

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
        "ticket_notified_no": row["ticket_notified_no"], "caught_up_notified_no": row["caught_up_notified_no"],
        "new_episode_seen_no": row["new_episode_seen_no"],
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


def get_kakao_pending_numbers(title_id: int) -> set[int]:
    """사용자가 "받지 않은 것으로" 기록해 둔, 시작 지점보다 앞이어도 자동으로 받을 회차 번호들(없거나 추적하지 않는 작품이면 빈 집합)."""
    row = fetchone("SELECT pending_numbers FROM kakao_webtoons WHERE title_id = ?", (title_id,))
    if row is None or not row["pending_numbers"]:
        return set()
    return {int(n) for n in json.loads(row["pending_numbers"])}


def set_kakao_pending_numbers(title_id: int, numbers) -> None:
    """받을 대상 지정을 통째로 바꾼다(빈 집합이면 지운다). 추적하지 않는 작품이면 아무것도 하지 않는다."""
    values = sorted({int(n) for n in numbers})
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET pending_numbers = ? WHERE title_id = ?", (json.dumps(values) if values else None, title_id))


def remove_kakao_pending_numbers(title_id: int, numbers) -> None:
    """받을 대상 지정에서 번호를 뺀다(회차를 받았을 때). 지정이 없으면 아무것도 하지 않는다."""
    current = get_kakao_pending_numbers(title_id)
    if current & {int(n) for n in numbers}:
        set_kakao_pending_numbers(title_id, current - {int(n) for n in numbers})


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


def list_kakao_origin_names() -> set[str]:
    """추적 중인 카카오 작품 어딘가에서 원작 작가로 나온 이름들."""
    names: set[str] = set()
    for row in fetchall("SELECT origin_names FROM kakao_webtoons"):
        names.update(json.loads(row["origin_names"] or "[]"))
    return names


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


def set_kakao_caught_up_notified(title_id: int, number: int) -> None:
    """"더 이상 받을 회차가 없습니다" 알림의 기준이 되는 마지막 회차 번호를 기록한다(알림을 보낸 때와, 이미 따라잡은 작품을 처음 확인했을 때)."""
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET caught_up_notified_no = ?, updated_at = ? WHERE title_id = ?", (number, _now(), title_id))


def set_kakao_new_episode_seen(title_id: int, count: int) -> None:
    """구독해제 웹툰의 새 에피소드 알림 기준(마지막으로 확인한 회차 수)을 기록한다."""
    with write_transaction() as conn:
        conn.execute("UPDATE kakao_webtoons SET new_episode_seen_no = ?, updated_at = ? WHERE title_id = ?", (count, _now(), title_id))


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
