"""카카오 신작 스캔: 등록한 작가의 새 작품을 구독 상태로 자동 추가한다(기준선/이미 추적 중인 작품/꺼진 작가/기능 끔 처리 포함)."""
import asyncio
from types import SimpleNamespace as NS

from app import db, repository, tracker, job_status, kakao_api, discord_notify

db.get_connection()
S = NS(request_timeout_seconds=5, delay_seconds=0)
sent = []; results = {}
async def fake_send(session, settings, message): sent.append(message)
async def fake_search(session, author_name, timeout): return list(results.get(author_name, []))
kakao_api.search_by_author = fake_search; discord_notify.send_webhook_notification = fake_send
_o = asyncio.sleep
async def _ns(x): await _o(0)
tracker.asyncio.sleep = _ns

def card(i, title, authors=("작가A",), thumb=None): return {"title_id": i, "title_name": title, "thumbnail_url": thumb or f"https://k/{i}", "author_names": list(authors), "is_adult": False}
async def scan(): job_status.start("discovery"); sent.clear(); return await tracker.scan_kakao_authors_for_new_titles(None, S)
def logs(): return "\n".join(job_status.snapshot()["discovery"]["log"])

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.upsert_watched_author("작가A", "작가A", True, "kakao"); repository.upsert_watched_author("꺼둔작가", "꺼둔작가", False, "kakao")
    results["작가A"] = [card(100, "기존작1"), card(101, "기존작2")]; results["꺼둔작가"] = [card(900, "안보는작")]

    # ── 1. 처음 스캔은 기준선만(자동 추가/알림 없음) ──
    assert await scan() == 0 and sent == [] and repository.get_kakao_webtoon(100) is None and "기준선으로만 저장" in logs()
    print("1) 기준선 OK")

    # ── 2. 새 작품이 나오면 구독 상태로 자동 추가 + 알림 ──
    results["작가A"].append(card(102, "신작", authors=("작가A", "다른작가"), thumb="https://k/102.jpg"))
    assert await scan() == 1
    w = repository.get_kakao_webtoon(102)
    assert w and w["status"] == "active" and w["ever_subscribed"] is True and w["thumbnail_url"] == "https://k/102.jpg" and w["author_summary"] == "작가A, 다른작가"
    assert len(sent) == 1 and "카카오웹툰 신작 자동 추가" in sent[0] and "신작" in sent[0] and "작가A" in sent[0] and "102" in sent[0] and "신작 자동 추가" in logs()
    assert repository.get_kakao_webtoon(100) is None                                                          # 기준선의 기존 작품은 추가하지 않는다
    print("2) 자동 추가 OK")

    # ── 3. 다시 스캔해도 중복 추가/알림 없음 ──
    assert await scan() == 0 and sent == []
    print("3) 중복 없음 OK")

    # ── 4. 이미 추적 중인 작품(제외/구독해제/목록)은 다시 추가하거나 알리지 않는다 ──
    repository.upsert_new_kakao_webtoon(103, "제외한작", status=repository.STATUS_ACTIVE)
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = 'excluded' WHERE title_id = 103")
    results["작가A"].append(card(103, "제외한작"))
    assert await scan() == 0 and sent == [] and repository.get_kakao_webtoon(103)["status"] == "excluded" and "이미 추적 중" in logs()
    print("4) 이미 추적 중인 작품 OK")

    # ── 5. 알림 전송이 실패해도 추가는 된다 ──
    async def boom(session, settings, message): raise RuntimeError("웹훅 오류")
    discord_notify.send_webhook_notification = boom; results["작가A"].append(card(104, "알림실패작"))
    assert await scan() == 1 and repository.get_kakao_webtoon(104)["status"] == "active"
    discord_notify.send_webhook_notification = fake_send
    print("5) 알림 실패 격리 OK")

    # ── 6. 꺼 둔 작가는 스캔하지 않는다 / 카카오웹툰 관리를 끄면 전체가 안 돈다 ──
    assert repository.get_kakao_webtoon(900) is None
    results["작가A"].append(card(105, "기능끔작")); repository.set_setting("kakao_webtoons_enabled", "0")
    assert await scan() == 0 and repository.get_kakao_webtoon(105) is None
    repository.set_setting("kakao_webtoons_enabled", "1"); assert await scan() == 1 and repository.get_kakao_webtoon(105)["status"] == "active"     # 다시 켜면 그때 발견
    print("6) 꺼 둔 작가/기능 끔 OK")
asyncio.run(main())
print("\n전부 통과")
