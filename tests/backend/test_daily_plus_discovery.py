"""네이버 매일+ 웹툰은 신작 스캔(작가/태그)이 새로 구독으로 추가하지 않고, 이미 자동 추가된 매일+ 작품은 "제외됨"으로 정리한다.

매일+는 화면과 검색에서만 숨겼더니, 신작 스캔이 새로 찾은 매일+ 작품을 구독 중으로 추가해 버려서 보이지도 않는 채로 자동 다운로드 대상이 됐다.
직접 구독한 매일+ 작품은 사용자가 고른 것이라 건드리지 않는다.
"""
import asyncio

from app import daily_plus, db, naver_api, repository, scheduler, tracker
from app import new_episode_watch
from app import cookie_health
from app.config import get_settings
db.get_connection()

settings = get_settings()
HIDDEN = {"852534", "852535", "852600", "852601", "852602", "852603"}   # 지금 알려진 매일+ titleId

sent = []
async def current_ids(timeout_seconds, session=None): return set(HIDDEN)
daily_plus.current_ids = current_ids
daily_plus.cached_ids = lambda: set(HIDDEN)

async def no_sleep(x): pass
asyncio.sleep = no_sleep

async def fake_send(session, message): sent.append(message); return True
tracker.send_webhook_notification = fake_send
async def fake_enrich(session, title_id, settings_, **k): return True, "ok"
tracker.enrich_one = fake_enrich
tracker._matches_enabled_writer = lambda other, ids: True
async def not_finished(session, other, title_id, settings_): return False
tracker._is_other_finished = not_finished

state = lambda tid: repository.get(tid).status if repository.exists(tid) else None


async def adding():
    # Scenario 1: 작가 신작(구독 작가/미구독 관심 작가 두 경로가 함께 쓰는 _add_discovered_titles) — 일반 작품은 추가하고 매일+ 작품은 추가하지 않는다
    others = [{"titleId": "111", "titleName": "일반 작품"}, {"titleId": "852534", "titleName": "매일+ 작품"}]
    added = await tracker._add_discovered_titles(None, settings, others, set())
    assert added == [("111", "일반 작품")], added
    assert state("111") == repository.STATUS_ACTIVE and not repository.exists("852534")
    print("1) 작가 신작: 매일+ 추가 안 함 OK")

    # Scenario 2: 태그 신작 — 마찬가지이고, 디스코드 알림에도 매일+ 작품이 나오지 않는다
    repository.upsert_watched_tag("77", "태그", enabled=True)
    async def fake_tag_name(session, tag_id, timeout): return "태그"
    async def fake_tag_titles(session, tag_id, timeout, delay): return [{"titleId": "201", "titleName": "일반 태그작"}, {"titleId": "852535", "titleName": "매일+ 태그작"}]
    naver_api.fetch_curation_title_name, naver_api.fetch_curation_titles = fake_tag_name, fake_tag_titles
    sent.clear()
    await tracker.scan_curation_tags(None, settings)
    assert state("201") == repository.STATUS_ACTIVE and not repository.exists("852535")
    assert len(sent) == 1 and "일반 태그작" in sent[0] and "매일+" not in sent[0], sent
    print("2) 태그 신작: 매일+ 추가 안 함 OK")

    # Scenario 3: 매일+ 목록을 한 번도 못 받아서 모르는 상태(빈 목록)에서는 아무것도 거르지 않는다 — 일반 작품이 사라지는 사고를 막고, 나중에 정리로 바로잡는다
    daily_plus.current_ids = lambda timeout_seconds, session=None: _empty()
    others = [{"titleId": "301", "titleName": "목록 모를 때 작품"}]
    assert await tracker._add_discovered_titles(None, settings, others, set()) == [("301", "목록 모를 때 작품")]
    daily_plus.current_ids = current_ids
    print("3) 목록을 모를 때는 거르지 않음 OK")


async def _empty(): return set()


def cleanup_rows():
    # 정리 대상: 구독 중(active)이고 자동으로 추가된(작가/태그) 매일+ 작품만
    def make(tid, title, source, status):
        repository.upsert_new(title_id=tid, title=title, added_source=source); repository.set_status(tid, status)
    make("852600", "작가가 추가한 매일+", repository.SOURCE_ARTIST, repository.STATUS_ACTIVE)
    make("852601", "태그가 추가한 매일+", repository.SOURCE_TAG, repository.STATUS_ACTIVE)
    make("852602", "직접 구독한 매일+", repository.SOURCE_MANUAL, repository.STATUS_ACTIVE)
    make("852603", "구독해제한 매일+", repository.SOURCE_ARTIST, repository.STATUS_UNSUBSCRIBED)
    make("401", "작가가 추가한 일반", repository.SOURCE_ARTIST, repository.STATUS_ACTIVE)


async def cleanup():
    cleanup_rows()
    # Scenario 4: 빈 목록이면 아무것도 안 한다
    assert repository.exclude_auto_added_titles(set()) == [] and state("852600") == repository.STATUS_ACTIVE
    # Scenario 5: 자동 추가된 구독 중 매일+만 "제외됨"으로 — 직접 구독한 것/구독해제한 것/일반 작품은 그대로
    done = await tracker.exclude_auto_added_daily_plus(None, settings)
    assert sorted(done) == [("852600", "작가가 추가한 매일+"), ("852601", "태그가 추가한 매일+")], done
    assert state("852600") == state("852601") == repository.STATUS_EXCLUDED
    assert state("852602") == repository.STATUS_ACTIVE and state("852603") == repository.STATUS_UNSUBSCRIBED and state("401") == repository.STATUS_ACTIVE
    # Scenario 6: 다시 해도 같은 결과(이미 제외됨은 건드리지 않음)
    assert await tracker.exclude_auto_added_daily_plus(None, settings) == []
    print("4~6) 이미 추가된 자동 추가 매일+ 정리 OK")


async def jobs():
    # Scenario 7: 다운로드 작업은 시작하기 전에 알려진 매일+ 목록(저장된 것, 네트워크 없이)으로 정리해서, 자동 추가된 매일+ 작품을 받지 않는다
    repository.upsert_new(title_id="852603", title="구독해제한 매일+"); repository.set_status("852603", repository.STATUS_UNSUBSCRIBED)
    repository.upsert_new(title_id="852777", title="새로 자동 추가된 매일+", added_source=repository.SOURCE_ARTIST)
    HIDDEN.add("852777")
    downloaded = []
    async def fake_download(session, settings_, title_id, adult_tracker, failures): downloaded.append(title_id)
    scheduler.download_job._download_new_episodes_for_one = fake_download   # _run_download_job_impl이 있는 download_job 모듈이 이 이름을 가져다 쓴다
    async def no_finalize(session, tracker_): return None
    cookie_health.finalize_and_notify = no_finalize
    await scheduler._run_download_job_impl({"naver"})
    assert "852777" not in downloaded and state("852777") == repository.STATUS_EXCLUDED, (downloaded, state("852777"))
    assert "111" in downloaded and "852602" in downloaded          # 일반 작품과 직접 구독한 매일+는 그대로 받는다
    # Scenario 8: 신작 스캔 작업도 시작할 때 정리한다
    calls = []
    async def noop(*a, **k): return 0
    async def fake_cleanup(session, settings_): calls.append("cleanup"); return []
    for name in ("backfill_missing_thumbnails", "scan_subscriptions_for_updates", "discover_titles_for_unlinked_watched_authors",
                 "scan_curation_tags", "refresh_inactive_metadata", "scan_kakao_authors_for_new_titles"):
        setattr(tracker, name, noop)
    tracker.exclude_auto_added_daily_plus = fake_cleanup
    scheduler.discovery_job._notify_newly_finished = noop
    async def fake_watch(session, settings_): return 0
    new_episode_watch.run = fake_watch
    await scheduler._run_discovery_job_impl()
    assert calls == ["cleanup"], calls
    print("7~8) 다운로드/신작 스캔 작업의 정리 연결 OK")

asyncio.run(adding())
asyncio.run(cleanup())
asyncio.run(jobs())
print("ALL OK")
