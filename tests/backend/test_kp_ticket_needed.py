"""카카오 자동 다운로드 — 다음 회차가 대여권이 필요해서 더 못 받을 때 디스코드로 알린다.

판정(RunResult.ticket_needed): 받을 회차를 다 받았고, 남은 첫 잠긴 회차가 기다무로 열 수 없는 회차(기다무가 없는 작품이거나 waitfree_blocked)일 때.
알림(scheduler._notify_kakao_ticket_needed): 같은 회차는 한 번만, 웹훅이 없거나 전송이 실패하면 기록하지 않는다.
"""
import asyncio

from app import db, discord_config, discord_notify, repository, scheduler
from app import kakao_page_download as kp
db.get_connection()


def ep(number, *, accessible=False, blocked=False, hidden=False, free_change_dt=None, subtitle=None):
    return kp.Episode(product_id=number, title=f"작품 {number}화", number=number, subtitle=subtitle or f"{number}화", is_free=False, accessible=accessible,
                      page_count=2, hidden=hidden, waitfree_blocked=blocked, free_change_dt=free_change_dt)


def result(*, to_download=(), locked=(), downloaded=(), ticket_used=None, failed=None, error=None, waitfree=True, title="작품"):
    plan = kp.DownloadPlan(mode="compare", to_download=list(to_download), locked=list(locked))
    return kp.RunResult(list(downloaded), [(n, f"{n}화") for n in downloaded], failed, plan, error, title=title, ticket_used=ticket_used, waitfree_supported=waitfree)


def judge():
    # Scenario 1: 기다무로 열 수 없는 회차(최신 회차 등)가 다음이면 대상이다
    r = result(locked=[ep(5, blocked=True)])
    assert r.ticket_needed is not None and r.ticket_needed.number == 5
    # Scenario 2: 기다무가 없는 작품은 잠긴 다음 회차가 곧 대여권 필요다
    assert result(locked=[ep(5)], waitfree=False).ticket_needed.number == 5
    # Scenario 3: 기다무로 열 수 있는 회차(충전 대기)는 계속 진행되니 알리지 않는다
    assert result(locked=[ep(5)], waitfree=True).ticket_needed is None
    # Scenario 4: 기다무로 막 열어서 받았다면 그 다음 잠긴 회차를 본다(열어 받은 회차는 건너뜀)
    r = result(downloaded=[5], ticket_used=5, locked=[ep(5), ep(6, blocked=True)])
    assert r.ticket_needed.number == 6
    assert result(downloaded=[5], ticket_used=5, locked=[ep(5), ep(6)]).ticket_needed is None
    assert result(downloaded=[5], ticket_used=5, locked=[ep(5)]).ticket_needed is None  # 더 잠긴 회차가 없음
    # Scenario 5: 받을 회차가 남았거나(상한) 실패/오류가 있으면 아직 판단하지 않는다
    assert result(to_download=[ep(4, accessible=True)], locked=[ep(5, blocked=True)]).ticket_needed is None
    assert result(locked=[ep(5, blocked=True)], failed=4).ticket_needed is None
    assert result(locked=[ep(5, blocked=True)], error="목록 실패").ticket_needed is None
    # Scenario 6: "N일 후 무료"(숨김 또는 무료 전환 예정) 회차는 기다리면 열리므로 대상이 아니다
    assert result(locked=[ep(5, blocked=True, hidden=True)]).ticket_needed is None
    assert result(locked=[ep(5, blocked=True, free_change_dt="2099-01-01T00:00:00+09:00")]).ticket_needed is None
    # Scenario 7: 잠긴 회차가 없으면 대상이 아니다
    assert result().ticket_needed is None
    print("판정 OK")


sent, state = [], {"ok": True}
async def fake_send(session, settings, message):
    if not discord_config.get_webhook_url() or not state["ok"]:
        return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send


async def notify(webtoon_id, r):
    wt = repository.get_kakao_webtoon(webtoon_id)
    await scheduler._notify_kakao_ticket_needed(None, None, wt, r)


async def notice():
    repository.upsert_new_kakao_webtoon(900, "작품")
    r5 = result(locked=[ep(5, blocked=True, subtitle="새 모험")], title="작품")

    # Scenario 8: 웹훅이 없으면 보내지 못하고 기록도 남기지 않는다(나중에 웹훅을 달면 바로 간다)
    await notify(900, r5)
    assert sent == [] and repository.get_kakao_webtoon(900)["ticket_notified_no"] is None
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")

    # Scenario 9: 전송에 실패하면 기록하지 않아 다음 실행에서 다시 시도한다
    state["ok"] = False
    await notify(900, r5)
    assert sent == [] and repository.get_kakao_webtoon(900)["ticket_notified_no"] is None
    state["ok"] = True

    # Scenario 10: 알림 문구, 그리고 같은 회차는 두 번째 실행에서 다시 알리지 않는다
    await notify(900, r5)
    assert sent == ["[작품] 다음 회차 (5번 '새 모험')부터는 대여권 충전이 필요합니다."], sent
    assert repository.get_kakao_webtoon(900)["ticket_notified_no"] == 5
    await notify(900, r5)
    assert len(sent) == 1

    # Scenario 11: 막힌 회차가 바뀌면(예: 5번을 받고 6번이 막힘) 다시 알린다
    await notify(900, result(locked=[ep(6, blocked=True, subtitle="이어지는 이야기")], title="작품"))
    assert len(sent) == 2 and sent[1].startswith("[작품] 다음 회차 (6번 '이어지는 이야기')") and repository.get_kakao_webtoon(900)["ticket_notified_no"] == 6

    # Scenario 12: 대상이 아니면 아무것도 보내지 않는다
    await notify(900, result(locked=[ep(7)]))
    assert len(sent) == 2
    print("알림 OK")


judge()
asyncio.run(notice())
print("ALL OK")
