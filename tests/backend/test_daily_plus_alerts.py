"""네이버 매일+ 작품은 디스코드 알림에도 나오지 않는다 — 리포트의 "미등록 웹툰 새 에피소드"와 "구독해제 웹툰 새 에피소드".

화면과 검색, 신작 스캔에서 빼도 알림이 남아 있으면 안 보이는 작품에 대한 알림만 오게 된다. 매일+ 목록을 모르면(빈 목록) 아무것도 거르지 않는다.
"""
import asyncio

from app import daily_plus, db, discord_config, discord_notify, naver_api, new_episode_watch, repository, scheduler
from app.config import get_settings
from app.models import EpisodeInfo, NaverListItem
db.get_connection()

settings = get_settings()
HIDDEN = {"852534"}
async def current_ids(timeout_seconds, session=None): return set(HIDDEN)
daily_plus.current_ids = current_ids
async def no_sleep(x): pass
asyncio.sleep = no_sleep


async def report_candidates():
    item = lambda tid, name: NaverListItem(title_id=tid, title_name=name, thumbnail_url="", weekdays=["MONDAY"], has_update=True)
    async def fake_list(session, timeout): return [item("111", "일반 미등록 작품"), item("852534", "매일+ 작품")]
    fetched = []
    async def fake_latest(session, title_id, timeout, cookies=None): fetched.append(title_id); return 7
    naver_api.fetch_full_webtoon_list, naver_api.fetch_latest_episode_no = fake_list, fake_latest

    # Scenario 1: 리포트의 미등록 새 에피소드 — 매일+는 후보에서 빠지고(최신 회차를 묻는 요청도 안 나간다) 일반 작품은 남는다
    got = await scheduler._collect_unregistered_new_episodes(None, settings)
    assert got == [("111", "일반 미등록 작품", 7)] and fetched == ["111"], (got, fetched)
    # Scenario 2: 매일+ 목록을 모르면 아무것도 거르지 않는다
    async def unknown(timeout_seconds, session=None): return set()
    daily_plus.current_ids = unknown; fetched.clear()
    got = await scheduler._collect_unregistered_new_episodes(None, settings)
    assert sorted(t for t, _, _ in got) == ["111", "852534"]
    daily_plus.current_ids = current_ids
    print("1~2) 리포트 미등록 새 에피소드 OK")


async def unsubscribed_watch():
    sent, fetched = [], []
    async def fake_send(session, settings_, message): sent.append(message); return True
    discord_notify.send_webhook_notification = fake_send
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    async def fake_latest(session, title_id, timeout, cookies=None):
        fetched.append(title_id); return EpisodeInfo(episode_no=12, subtitle="열두 번째", is_locked=False)
    naver_api.fetch_latest_episode = fake_latest
    # 카카오 쪽은 쿠키가 없으면 건너뛰므로 이 테스트에서는 쓰지 않는다
    new_episode_watch.kakao_page_download.client_from_saved_cookies = lambda session, timeout: None
    for tid, name in (("1", "일반 구독해제"), ("852534", "매일+ 구독해제")):
        repository.upsert_new(tid, name); repository.set_status(tid, repository.STATUS_UNSUBSCRIBED)
        repository.set_naver_new_episode_seen(tid, 10)        # 이미 기준이 있어서 12화가 새 에피소드

    # Scenario 3: 구독해제 새 에피소드 — 매일+ 작품은 확인도 알림도 하지 않고 일반 작품만 처리한다
    assert await new_episode_watch.run(None, settings) == 1
    assert fetched == ["1"] and len(sent) == 1 and "일반 구독해제" in sent[0] and "매일+" not in sent[0], (fetched, sent)
    assert repository.get("852534").new_episode_seen_no == 10        # 매일+ 작품의 기준은 그대로
    print("3) 구독해제 새 에피소드 OK")

asyncio.run(report_candidates())
asyncio.run(unsubscribed_watch())
print("ALL OK")
