"""카카오 자동 다운로드 — 연재작을 최신 회차까지 다 받아서 더 받을 회차가 없으면 디스코드로 알린다(완결작은 기존 완결 확인 알림이 맡는다).

판정(RunResult.caught_up_episode): 연재 중이고, 이번 실행 뒤 받을 회차도 잠긴 회차도 없을 때 마지막 회차.
알림(scheduler._notify_kakao_caught_up): 같은 마지막 회차는 한 번만. 이미 따라잡은 채로 처음 확인되는 작품은 조용히 기준만 기록하고(안 그러면
구독 중인 연재작 전부에 알림이 한꺼번에 간다), 웹훅이 없거나 전송이 실패하면 기록하지 않는다.
"""
import asyncio

from app import db, discord_config, discord_notify, repository, scheduler
from app import kakao_page_download as kp
db.get_connection()


def ep(number, *, accessible=True, subtitle=None):
    return kp.Episode(product_id=number, title=f"작품 {number}화", number=number, subtitle=subtitle or f"{number}화", is_free=accessible, accessible=accessible, page_count=2, hidden=False)


def result(numbers, *, locked=(), to_download=(), downloaded=(), failed=None, error=None, finished=False, ticket_used=None, title="작품"):
    rows = [kp.EpisodeRow(ep(n), downloaded=True) for n in numbers]
    plan = kp.DownloadPlan(mode="compare", rows=rows, to_download=[ep(n) for n in to_download], locked=[ep(n, accessible=False) for n in locked])
    return kp.RunResult(list(downloaded), [(n, f"{n}화") for n in downloaded], failed, plan, error, title=title, ticket_used=ticket_used, finished=finished)


def judge():
    # Scenario 1: 연재작을 다 받았으면 마지막 회차가 나온다
    assert result([1, 2, 3]).caught_up_episode.number == 3
    # Scenario 2: 완결작은 대상이 아니다(기존 완결 확인 알림이 맡는다)
    assert result([1, 2, 3], finished=True).caught_up_episode is None
    # Scenario 3: 잠긴 회차가 남았거나, 받을 회차가 남았거나, 실패/오류면 아직 아니다
    assert result([1, 2, 3], locked=[3]).caught_up_episode is None
    assert result([1, 2, 3], to_download=[3], downloaded=[]).caught_up_episode is None
    assert result([1, 2, 3], failed=3).caught_up_episode is None and result([1, 2, 3], error="목록 실패").caught_up_episode is None
    # Scenario 4: 마지막 잠긴 회차를 기다무로 열어 받았다면 다 받은 것이다
    assert result([1, 2, 3], locked=[3], downloaded=[3], ticket_used=3).caught_up_episode.number == 3
    # Scenario 5: 회차가 하나도 없으면 알릴 것이 없다
    assert result([]).caught_up_episode is None
    print("판정 OK")


sent, state = [], {"ok": True}
async def fake_send(session, settings, message):
    if not discord_config.get_webhook_url() or not state["ok"]:
        return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send


async def notify(r):
    await scheduler._notify_kakao_caught_up(None, None, repository.get_kakao_webtoon(910), r)
notified = lambda: repository.get_kakao_webtoon(910)["caught_up_notified_no"]


async def notice():
    repository.upsert_new_kakao_webtoon(910, "작품")

    # Scenario 6: 이미 따라잡은 채로 처음 확인되면(이번에 받은 회차 없음) 알리지 않고 기준만 기록한다
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    await notify(result([1, 2, 3]))
    assert sent == [] and notified() == 3

    # Scenario 7: 새 회차를 받아서 따라잡으면 알린다 — 문구, 그리고 같은 회차는 다시 알리지 않는다
    await notify(result([1, 2, 3, 4], downloaded=[4]))
    assert sent == ["[작품] 더 이상 받을 회차가 없습니다. (마지막 4번 '4화')"], sent
    assert notified() == 4
    await notify(result([1, 2, 3, 4]))
    assert len(sent) == 1

    # Scenario 8: 전송에 실패하면 기록하지 않아 다음 실행에서 다시 시도한다
    state["ok"] = False
    await notify(result([1, 2, 3, 4, 5], downloaded=[5]))
    assert len(sent) == 1 and notified() == 4
    state["ok"] = True
    await notify(result([1, 2, 3, 4, 5]))   # 다음 실행: 이번엔 받은 게 없어도 기록된 번호와 다르니 알린다
    assert len(sent) == 2 and sent[1].endswith("(마지막 5번 '5화')") and notified() == 5

    # Scenario 9: 대상이 아니면(완결/잠긴 회차 남음) 아무것도 보내지 않는다
    await notify(result([1, 2, 3, 4, 5, 6], downloaded=[6], finished=True))
    await notify(result([1, 2, 3, 4, 5, 6], downloaded=[6], locked=[6]))
    assert len(sent) == 2 and notified() == 5
    print("알림 OK")


judge()
asyncio.run(notice())
print("ALL OK")
