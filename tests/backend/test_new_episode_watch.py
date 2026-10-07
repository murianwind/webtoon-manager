"""구독해제 웹툰의 새 에피소드를 신작 스캔 때 디스코드로 알린다(네이버 + 카카오).

- 네이버: 가장 최근 회차 번호가 기록해 둔 번호보다 커지면 새 에피소드. 카카오: 작품 정보의 회차 수(on_sale_count)가 늘면 새 에피소드.
- 처음 확인하는 작품은 알리지 않고 기준만 기록한다(안 그러면 구독해제 작품 전부에 알림이 한꺼번에 간다).
- 알림은 한 메시지로 묶어 보내고, 전송에 성공했을 때만 기록한다(웹훅이 없거나 실패하면 다음 스캔에서 다시).
"""
import asyncio

from app import db, discord_config, discord_notify, naver_api, repository, scheduler, tracker
from app import kakao_page_download as kp
from app import new_episode_watch as watch
from app.config import get_settings
from app.models import EpisodeInfo
db.get_connection()

settings = get_settings()


def decide_table():
    # Scenario 1: 판정 규칙 — 처음이면 기준만, 커졌을 때만 알림, 못 가져왔거나 그대로/줄었으면 아무것도 안 한다
    assert watch.decide(None, 10) == (False, 10)
    assert watch.decide(10, 11) == (True, 11)
    assert watch.decide(10, 10) == (False, None)
    assert watch.decide(10, 9) == (False, None)
    assert watch.decide(None, None) == (False, None) and watch.decide(10, None) == (False, None)
    print("1) 판정 규칙 OK")


LATEST = {}      # 네이버 title_id -> 최신 회차(None이면 조회 실패)
FETCHED = []
async def fake_latest(session, title_id, timeout_seconds, cookies=None):
    FETCHED.append(title_id)
    value = LATEST.get(title_id)
    return None if value is None else EpisodeInfo(episode_no=value[0], subtitle=value[1], is_locked=False)
naver_api.fetch_latest_episode = fake_latest

KAKAO = {}       # 카카오 series_id -> on_sale_count(None이면 조회 실패)
class FakeKakaoClient:
    async def fetch_series_item(self, series_id):
        count = KAKAO.get(series_id)
        return None if count is None else {"title": "x", "on_sale_count": count}
kp.client_from_saved_cookies = lambda session, timeout: FakeKakaoClient()
kp.persist_refreshed_cookies = lambda client: None

sent, state = [], {"ok": True}
async def fake_send(session, settings_, message):
    if not discord_config.get_webhook_url() or not state["ok"]:
        return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send

async def no_sleep(x): pass
watch.asyncio.sleep = no_sleep


def naver_seen(title_id): return repository.get(title_id).new_episode_seen_no
def kakao_seen(series_id): return repository.get_kakao_webtoon(series_id)["new_episode_seen_no"]


async def main():
    # 준비: 네이버 구독해제 2개 + 제외됨 1개 + 구독 중 1개, 카카오 구독해제 1개 + 구독 중 1개
    for tid, name, status in (("1", "네이버A", repository.STATUS_UNSUBSCRIBED), ("2", "네이버B", repository.STATUS_UNSUBSCRIBED),
                              ("3", "제외작", repository.STATUS_EXCLUDED), ("4", "구독중작", repository.STATUS_ACTIVE)):
        repository.upsert_new(tid, name); repository.set_status(tid, status)
    repository.upsert_new_kakao_webtoon(101, "카카오A", status=repository.STATUS_UNSUBSCRIBED)
    repository.upsert_new_kakao_webtoon(102, "카카오구독중", status=repository.STATUS_ACTIVE)
    LATEST.update({"1": (10, "열 번째 이야기"), "2": (5, "다섯 번째"), "3": (7, "x"), "4": (9, "x")})
    KAKAO.update({101: 30, 102: 40})
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")

    # Scenario 2: 처음 확인하는 작품은 알리지 않고 기준만 기록한다 — 구독 중/제외됨 작품은 조회하지도 않는다
    assert await watch.run(None, settings) == 0 and sent == []
    assert naver_seen("1") == 10 and naver_seen("2") == 5 and kakao_seen(101) == 30
    assert naver_seen("3") is None and naver_seen("4") is None and kakao_seen(102) is None
    assert sorted(FETCHED) == ["1", "2"], FETCHED
    print("2) 처음 확인: 기준만 기록 OK")

    # Scenario 3: 새 회차가 올라온 작품만, 네이버/카카오를 한 메시지로 묶어 알린다
    LATEST["1"] = (11, "열한 번째 이야기"); KAKAO[101] = 31
    assert await watch.run(None, settings) == 2
    assert len(sent) == 1, sent
    assert sent[0] == "🔔 **구독해제 웹툰 새 에피소드**\n[네이버/네이버A] 11화 '열한 번째 이야기'\n[카카오/카카오A] 새 회차가 올라왔습니다 (현재 31회차)", sent[0]
    assert naver_seen("1") == 11 and naver_seen("2") == 5 and kakao_seen(101) == 31
    print("3) 새 회차 알림 OK")

    # Scenario 4: 같은 회차는 다시 알리지 않는다
    assert await watch.run(None, settings) == 0 and len(sent) == 1
    print("4) 중복 알림 없음 OK")

    # Scenario 5: 조회에 실패한 작품은 기록을 건드리지 않고, 다음에 정상이면 그때 알린다
    LATEST["2"] = None
    assert await watch.run(None, settings) == 0 and naver_seen("2") == 5
    LATEST["2"] = (6, "여섯 번째")
    assert await watch.run(None, settings) == 1 and sent[-1].endswith("[네이버/네이버B] 6화 '여섯 번째'") and naver_seen("2") == 6
    print("5) 조회 실패 처리 OK")

    # Scenario 6: 전송에 실패하면 기록하지 않아 다음 스캔에서 다시 알린다
    LATEST["1"] = (12, "열두 번째"); before = len(sent)
    state["ok"] = False
    assert await watch.run(None, settings) == 0 and naver_seen("1") == 11 and len(sent) == before
    state["ok"] = True
    assert await watch.run(None, settings) == 1 and naver_seen("1") == 12 and "12화 '열두 번째'" in sent[-1]
    print("6) 전송 실패 시 재시도 OK")

    # Scenario 7: 신작 스캔(discovery job)이 이 확인을 실제로 부른다
    calls = []
    async def noop(*a, **k): return 0
    async def fake_run(session, settings_): calls.append("watch"); return 0
    for name in ("backfill_missing_thumbnails", "scan_subscriptions_for_updates", "discover_titles_for_unlinked_watched_authors",
                 "scan_curation_tags", "refresh_inactive_metadata", "scan_kakao_authors_for_new_titles"):
        setattr(tracker, name, noop)
    scheduler._notify_newly_finished = noop
    scheduler.new_episode_watch.run = fake_run
    await scheduler._run_discovery_job_impl()
    assert calls == ["watch"], calls
    print("7) 신작 스캔에 연결 OK")

decide_table()
asyncio.run(main())
print("ALL OK")
