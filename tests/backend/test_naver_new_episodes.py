"""리포트의 "웹툰 전체목록 중 새 에피소드"(네이버) — 전체목록에 보이는 미구독 작품만 알린다(카카오와 같은 규칙).
기록 없음 / "목록" 상태(구독한 적 있는데 목록으로 되돌린 작품)는 알리고, 구독 중·구독해제·제외됨은 알리지 않는다."""
import asyncio
from types import SimpleNamespace as NS

from app import db, naver_api, repository, scheduler
from app.config import get_settings
db.get_connection()

S = NS(request_timeout_seconds=5, artist_scan_concurrency=3, delay_seconds=0, cookie_file_path=get_settings().cookie_file_path)
ITEMS = [NS(title_id=t, title_name=f"작품{t}", has_update=up, is_adult=False) for t, up in
         (("1", True), ("2", True), ("3", True), ("4", True), ("5", True), ("6", True), ("7", False))]
async def fake_list(session, timeout): return list(ITEMS)
async def fake_latest(session, title_id, timeout, cookies=None): return 100 + int(title_id)
naver_api.fetch_full_webtoon_list = fake_list; naver_api.fetch_latest_episode_no = fake_latest

def add(title_id, status, ever_subscribed):
    repository.upsert_new(title_id=title_id, title=f"작품{title_id}", added_source=repository.SOURCE_MANUAL)
    with db.write_transaction() as cx:
        cx.execute("UPDATE webtoons SET status = ?, ever_subscribed = ? WHERE title_id = ?", (status, 1 if ever_subscribed else 0, title_id))

async def main():
    # 1: 기록 없음(전체목록에만 있음)
    add("2", repository.STATUS_ACTIVE, True)            # 구독 중 → 다운로드로 알리므로 여기선 안 알림
    add("3", repository.STATUS_UNSUBSCRIBED, True)      # 구독해제 → 안 알림
    add("4", repository.STATUS_EXCLUDED, False)         # 제외됨 → 안 알림
    add("5", repository.STATUS_UNREGISTERED, True)      # 구독한 적 있는데 "목록으로" 되돌림 → 전체목록에 보이니 알림
    # 6: 기록 없음(제외됨에서 "목록으로" 보낸, 구독한 적 없는 작품 — 이때는 기록이 지워진다)
    # 7: 새 회차 없음 → 후보 아님
    got = await scheduler._collect_unregistered_new_episodes(None, S)
    assert sorted(t for t, _, _ in got) == ["1", "5", "6"], got
    assert dict((t, no) for t, _, no in got) == {"1": 101, "5": 105, "6": 106}
    print("네이버 새 에피소드 후보 OK (기록 없음 + 목록 상태만, 구독 중/구독해제/제외됨 제외)")
asyncio.run(main())
print("\n전부 통과")
