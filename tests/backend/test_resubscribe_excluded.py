"""신작 스캔의 구독 규칙 — 등록한 작가/태그에 해당하는 작품 중
  · 제외됨 + 구독 이력 없음 + 완결 아님 → 구독한다(목록제외로 보낸 작품 포함). 알림에 "제외됨에서 구독"을 표시한다.
  · 제외됨 + 구독 이력 있음 → 다시 구독하지 않는다(구독 중에 "제외됨으로"를 눌렀거나, 구독했다가 제외한 작품).
  · 구독해제/목록(미등록)/구독 중/완결/매일+/등록 안 된 작가의 작품은 그대로다.
사용자가 제외됨 작품을 직접 다시 구독하면 구독 중이 되어 제외됨 상태는 사라지고(이력은 남음), 나중에 다시 제외하면 강제 제외로 자리 잡는다.
네이버(작가 두 경로가 공유하는 _add_discovered_titles, 태그)와 카카오(작가) 모두 같은 규칙이다."""
import asyncio
from types import SimpleNamespace as NS

import httpx

from app import daily_plus, db, discord_notify, kakao_api, naver_api, repository, tracker
from app.config import get_settings
db.get_connection()
import app.main as m

settings = get_settings()
sent = []
async def fake_send(session, message): sent.append(message); return True
tracker.send_webhook_notification = fake_send; discord_notify.send_webhook_notification = fake_send
async def fake_enrich(session, title_id, settings_, **k): return True, "ok"
tracker.enrich_one = fake_enrich
async def current_ids(timeout_seconds, session=None): return {"705", "805"}          # 매일+
daily_plus.current_ids = current_ids
FINISHED = {"703"}
tracker._matches_enabled_writer = lambda other, ids: str(other["titleId"]) != "704"
async def is_finished(session, other, title_id, settings_): return title_id in FINISHED
tracker._is_other_finished = is_finished
_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns

status = lambda tid: repository.get(tid).status
subscribed_ever = lambda tid: repository.get(tid).ever_subscribed


def naver_row(tid, title, state, history):
    """history=True: 구독한 적이 있다(구독 중이었다가 상태가 바뀜), False: 구독 없이 그 상태가 됐다(예: 목록제외)."""
    repository.upsert_new(tid, title, False, None, repository.SOURCE_MANUAL, "", history)
    repository.set_status(tid, state)


async def naver_authors():
    naver_row("701", "제외 이력없음", repository.STATUS_EXCLUDED, False)
    naver_row("702", "제외 이력있음", repository.STATUS_EXCLUDED, True)
    naver_row("703", "제외 이력없음 완결", repository.STATUS_EXCLUDED, False)
    naver_row("704", "제외 이력없음 작가불일치", repository.STATUS_EXCLUDED, False)
    naver_row("705", "제외 이력없음 매일플러스", repository.STATUS_EXCLUDED, False)
    naver_row("706", "구독해제", repository.STATUS_UNSUBSCRIBED, True)
    naver_row("707", "목록(미등록)", repository.STATUS_UNREGISTERED, True)
    naver_row("708", "구독 중", repository.STATUS_ACTIVE, True)
    others = [{"titleId": t, "titleName": n} for t, n in (("701", "제외 이력없음"), ("702", "제외 이력있음"), ("703", "완결"), ("704", "불일치"), ("705", "매일+"),
                                                       ("706", "구독해제"), ("707", "목록"), ("708", "구독 중"), ("709", "새 작품"))]
    # Scenario 1: 작가 신작(구독 작가/미구독 관심 작가 두 경로가 함께 쓰는 함수)
    added = await tracker._add_discovered_titles(None, settings, others, set())
    assert added == [("701", "제외 이력없음 (제외됨에서 구독)"), ("709", "새 작품")], added
    assert status("701") == repository.STATUS_ACTIVE and subscribed_ever("701") is True and status("709") == repository.STATUS_ACTIVE
    assert [status(t) for t in ("702", "703", "704", "705")] == [repository.STATUS_EXCLUDED] * 4        # 이력 있음/완결/작가 불일치/매일+는 그대로
    assert [status(t) for t in ("706", "707", "708")] == [repository.STATUS_UNSUBSCRIBED, repository.STATUS_UNREGISTERED, repository.STATUS_ACTIVE]
    print("1) 네이버 작가 신작 OK")


async def naver_tags():
    naver_row("801", "태그 제외 이력없음", repository.STATUS_EXCLUDED, False)
    naver_row("802", "태그 제외 이력있음", repository.STATUS_EXCLUDED, True)
    naver_row("803", "태그 제외 이력없음 완결", repository.STATUS_EXCLUDED, False)
    naver_row("805", "태그 제외 이력없음 매일플러스", repository.STATUS_EXCLUDED, False)
    repository.upsert_watched_tag("77", "태그", enabled=True)
    async def tag_name(session, tag_id, timeout): return "태그"
    async def tag_titles(session, tag_id, timeout, delay):
        return [{"titleId": "801", "titleName": "태그 제외 이력없음"}, {"titleId": "802", "titleName": "태그 제외 이력있음"},
                {"titleId": "803", "titleName": "완결", "finished": True}, {"titleId": "805", "titleName": "매일+"}, {"titleId": "806", "titleName": "태그 새 작품"}]
    naver_api.fetch_curation_title_name, naver_api.fetch_curation_titles = tag_name, tag_titles
    sent.clear()
    # Scenario 2: 태그 신작 — 같은 규칙, 알림 줄에 "제외됨에서 구독" 표시
    await tracker.scan_curation_tags(None, settings)
    assert status("801") == repository.STATUS_ACTIVE and subscribed_ever("801") is True and status("806") == repository.STATUS_ACTIVE
    assert [status(t) for t in ("802", "803", "805")] == [repository.STATUS_EXCLUDED] * 3
    assert len(sent) == 1 and "태그 제외 이력없음" in sent[0] and "제외됨에서 구독" in sent[0] and "태그 새 작품" in sent[0] and "완결" not in sent[0] and "매일+" not in sent[0], sent
    print("2) 네이버 태그 신작 OK")


async def button_flow():
    others = lambda tid, name: [{"titleId": tid, "titleName": name}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # Scenario 3: 구독 중 → "제외됨으로" 버튼(제외 API) → 구독 이력은 유지되므로 신작 스캔이 다시 구독하지 않는다(강제 제외)
        assert (await c.post("/api/naver-list/901/subscribe", json={"title": "버튼 작품", "thumbnail_url": ""})).json()["status"] == "active"
        r = (await c.post("/api/naver-list/901/exclude", json={"title": "버튼 작품", "thumbnail_url": ""})).json()
        assert r["status"] == "excluded" and r["ever_subscribed"] is True, r
        assert await tracker._add_discovered_titles(None, settings, others("901", "버튼 작품"), set()) == [] and status("901") == repository.STATUS_EXCLUDED
        # Scenario 4: 사용자가 직접 다시 구독하면 구독 중이 되고(제외됨 상태는 사라짐), 다시 제외하면 또 강제 제외다
        assert (await c.post("/api/naver-list/901/subscribe", json={"title": "버튼 작품", "thumbnail_url": ""})).json()["status"] == "active"
        assert status("901") == repository.STATUS_ACTIVE
        await c.post("/api/naver-list/901/exclude", json={"title": "버튼 작품", "thumbnail_url": ""})
        assert await tracker._add_discovered_titles(None, settings, others("901", "버튼 작품"), set()) == [] and status("901") == repository.STATUS_EXCLUDED
        # Scenario 5: 구독한 적 없이 "목록제외"로 보낸 작품은 이력이 없어서 규칙대로 다시 구독된다(사용자가 정한 규칙)
        r = (await c.post("/api/naver-list/902/exclude", json={"title": "목록제외 작품", "thumbnail_url": ""})).json()
        assert r["status"] == "excluded" and r["ever_subscribed"] is False
        assert await tracker._add_discovered_titles(None, settings, others("902", "목록제외 작품"), set()) == [("902", "목록제외 작품 (제외됨에서 구독)")]
    print("3~5) 버튼/직접 재구독/목록제외 흐름 OK")


S = NS(request_timeout_seconds=5, delay_seconds=0)
results = {}
async def fake_search(session, author_name, timeout): return list(results.get(author_name, []))
kakao_api.search_by_author = fake_search
async def no_authors(title_id, settings_): return None
tracker._record_kakao_title_authors = no_authors


def kcard(i, title, finished=False): return {"title_id": i, "title_name": title, "is_adult": False, "is_finished": finished}
def kakao_row(tid, title, state, history):
    repository.upsert_new_kakao_webtoon(tid, title, status=repository.STATUS_ACTIVE if history else state)
    if history: repository.set_kakao_webtoon_status(tid, state)
kstatus = lambda tid: repository.get_kakao_webtoon(tid)["status"]


async def kakao_authors():
    from app import job_status
    job_status.start("discovery")
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.upsert_watched_author("작가A", "작가A", True, "kakao"); repository.upsert_watched_author("작가B", "작가B", True, "kakao")
    # 작가A는 이미 한 번 스캔한 상태(기준선 있음)
    results["작가A"] = [kcard(1100, "기존작")]
    await tracker.scan_kakao_authors_for_new_titles(None, S)
    kakao_row(1101, "제외 이력없음", repository.STATUS_EXCLUDED, False)
    kakao_row(1102, "제외 이력있음", repository.STATUS_EXCLUDED, True)
    kakao_row(1103, "제외 이력없음 완결", repository.STATUS_EXCLUDED, False)
    kakao_row(1104, "구독해제", repository.STATUS_UNSUBSCRIBED, True)
    results["작가A"] = [kcard(1100, "기존작"), kcard(1101, "제외 이력없음"), kcard(1102, "제외 이력있음"), kcard(1103, "제외 이력없음 완결", finished=True),
                      kcard(1104, "구독해제"), kcard(1105, "새 작품"), kcard(1106, "새 완결작", finished=True)]
    sent.clear()
    # Scenario 6: 제외됨 + 이력 없음 + 완결 아님 → 구독(이미 본 작품이어도), 이력 있음/완결/구독해제는 그대로, 새 완결작은 추가하지 않는다
    assert await tracker.scan_kakao_authors_for_new_titles(None, S) == 2
    assert kstatus(1101) == "active" and repository.get_kakao_webtoon(1101)["ever_subscribed"] is True and kstatus(1105) == "active"
    assert kstatus(1102) == "excluded" and kstatus(1103) == "excluded" and kstatus(1104) == "unsubscribed" and repository.get_kakao_webtoon(1106) is None
    assert len(sent) == 2 and any("제외 이력없음" in s and "제외됨에서 구독" in s for s in sent) and any("새 작품" in s and "제외됨에서 구독" not in s for s in sent), sent
    # Scenario 7: 새 완결작도 "본 작품"으로는 기록되어 다음 스캔에서 다시 보지 않고, 다시 돌려도 중복 알림이 없다
    assert 1106 in repository.get_seen_kakao_title_ids("작가A")
    sent.clear(); assert await tracker.scan_kakao_authors_for_new_titles(None, S) == 0 and sent == []
    # Scenario 8: 처음 스캔하는 작가(기준선)여도 제외됨 + 이력 없음 + 완결 아님은 구독하고, 나머지 기존 작품은 기준선으로만 저장한다
    kakao_row(1201, "처음 스캔 제외 이력없음", repository.STATUS_EXCLUDED, False)
    results["작가B"] = [kcard(1201, "처음 스캔 제외 이력없음"), kcard(1202, "처음 스캔 새 작품")]
    assert await tracker.scan_kakao_authors_for_new_titles(None, S) == 1
    assert kstatus(1201) == "active" and repository.get_kakao_webtoon(1202) is None
    print("6~8) 카카오 작가 신작 OK")

asyncio.run(naver_authors())
asyncio.run(naver_tags())
asyncio.run(button_flow())
asyncio.run(kakao_authors())
print("ALL OK")
