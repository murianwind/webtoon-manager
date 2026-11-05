"""다운로드 리포트: 등록 안 한 작품의 "새 에피소드"는 같은 회차를 한 번만 알린다(하루에 여러 번 받아도 반복 안 됨).
자동 발송(send_batch_report)은 받은 이력이 있을 때만 나가고 새 에피소드 요약은 그 이력과 함께 나간다 — 그래서 매 묶음마다 새 다운로드 이력을 하나 넣고 보낸다.
이력이 없으면(with_row=False) 새 에피소드가 있어도 아무것도 안 보낸다. 테스트 발송(run_test_report)은 요약만 있어도 보내고 기록을 남기지 않는다."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify
db.get_connection()
# 이 테스트는 리포트에 담기는 섹션의 내용/반복 여부를 본다 — 언제 얹히는지(하루 한 번)는 test_list_alert.py/test_list_alert_flow.py가 본다. 그래서 매 묶음에 얹히게 둔다.
from app import list_alert
list_alert.should_attach = lambda now: True
import app.scheduler as _scheduler                                                                                 # 신규 웹툰 수집은 이 테스트의 관심사가 아니라 비워 둔다(실제 네트워크를 부르지 않게)
async def _no_new_titles(session, settings): return []
_scheduler.report_sections.collect_naver_new_titles = _no_new_titles; _scheduler.report_sections.collect_kakao_new_titles = _no_new_titles

sent = []; FAIL = {"on": False}
async def fake_send(session, message):
    if FAIL["on"]: raise RuntimeError("웹훅 전송 실패")
    sent.append(message)
discord_notify.send_webhook_notification = fake_send
NAVER = []; KAKAO = []
async def fake_naver(session, settings): return list(NAVER)
async def fake_kakao(session, settings): return list(KAKAO)
scheduler.report_sections.collect_unregistered_new_episodes = fake_naver; scheduler.report_sections.collect_kakao_new_episodes = fake_kakao

counter = [0]
async def report(force=False, with_row=True):
    """한 번의 발송 — 자동 발송이면 새로 받은 이력(성공 1건)을 넣고 묶음의 끝을 지금으로 해서 보낸다."""
    job_status.start("report"); before = len(sent)
    if force:
        await scheduler.run_test_report()
    else:
        if with_row:
            counter[0] += 1; repository.add_episode_history("90000", f"받은작품{counter[0]}", counter[0], f"{counter[0]}화", "success", platform="naver")
        await scheduler.send_batch_report(datetime.now(timezone.utc).isoformat())
    return sent[before:], job_status.snapshot()["report"]["status"]

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.set_setting(scheduler._SETTING_KEY_REPORT_LAST_SENT_AT, (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    NAVER[:] = [("10", "네이버신작", 50)]; KAKAO[:] = [(7, "카카오신작", "https://page.kakao.com/content/7/viewer/700")]
    has_new = lambda m, *titles: all(t in m for t in titles)

    # ── 1. 처음엔 이력과 함께 둘 다 알린다 ──
    msgs, st = await report()
    assert len(msgs) == 1 and has_new(msgs[0], "받은작품1", "네이버신작", "카카오신작") and st == "success"
    # ── 2. 같은 회차를 다시 받아도 반복하지 않는다(이력만 나가고, 이력이 없으면 메시지 자체를 안 보냄) ──
    msgs, st = await report(); assert len(msgs) == 1 and "받은작품2" in msgs[0] and "네이버신작" not in msgs[0] and "카카오신작" not in msgs[0] and st == "success", msgs
    msgs, st = await report(with_row=False); assert msgs == [] and st == "success"
    print("1) 같은 새 에피소드는 한 번만 OK")

    # ── 3. 더 새로운 회차가 나오면 그 작품만 다시 알린다 ──
    NAVER[:] = [("10", "네이버신작", 51)]
    msgs, _ = await report(); assert len(msgs) == 1 and "네이버신작" in msgs[0] and "카카오신작" not in msgs[0], msgs
    KAKAO[:] = [(7, "카카오신작", "https://page.kakao.com/content/7/viewer/701")]
    msgs, _ = await report(); assert len(msgs) == 1 and "카카오신작" in msgs[0] and "네이버신작" not in msgs[0]
    KAKAO[:] = [(7, "카카오신작(제목만 바뀜)", "https://page.kakao.com/content/7/viewer/701")]      # 같은 회차 주소면 제목이 바뀌어도 반복 안 함
    msgs, _ = await report(); assert len(msgs) == 1 and "카카오신작" not in msgs[0]
    print("2) 새 회차가 나오면 다시 알림 OK (작품별)")

    # ── 4. 다른 작품이 추가되면 그것만 ──
    NAVER[:] = [("10", "네이버신작", 51), ("11", "다른신작", 3)]
    msgs, _ = await report(); assert len(msgs) == 1 and "다른신작" in msgs[0] and "네이버신작" not in msgs[0]
    print("3) 새 작품만 OK")

    # ── 5. 다운로드 기록이 있는 리포트에는 기록은 나오고, 이미 알린 새 에피소드는 안 나온다 ──
    repository.add_episode_history("61641075", "쌍갑포차", 409, "409화", "success", platform="kakao")
    msgs, _ = await report()
    assert len(msgs) == 1 and "쌍갑포차" in msgs[0] and "네이버신작" not in msgs[0] and "다른신작" not in msgs[0] and "카카오신작" not in msgs[0], msgs
    print("4) 다운로드 기록과 함께여도 반복 안 함 OK")

    # ── 6. 전송이 실패하면 "알렸다"고 기록하지 않아서 다음에 다시 시도한다(이력도 다음 묶음에 다시 담긴다) ──
    NAVER[:] = [("12", "실패테스트작", 9)]
    FAIL["on"] = True; msgs, st = await report(); assert msgs == [] and st == "error"
    FAIL["on"] = False; msgs, st = await report(with_row=False); assert len(msgs) == 1 and "실패테스트작" in msgs[0] and st == "success", "실패한 묶음의 이력/요약은 다음에 다시 나간다"
    print("5) 전송 실패 시 재시도 OK")

    # ── 7. 테스트 발송은 이미 알린 것도 보여주고(이력 없이 요약만 있어도 보낸다), 기록도 남기지 않는다 ──
    msgs, _ = await report(force=True); assert len(msgs) == 1 and "실패테스트작" in msgs[0]
    NAVER[:] = [("13", "테스트발송후작", 4)]
    msgs, _ = await report(force=True); assert "테스트발송후작" in msgs[0]
    msgs, _ = await report(); assert len(msgs) == 1 and "테스트발송후작" in msgs[0]            # 테스트 발송은 기록하지 않았으니 정상 리포트에 나온다
    print("6) 테스트 발송 OK (필터/기록 안 함)")

    # ── 8. 기억하는 목록은 무한히 커지지 않는다 ──
    from app import report_seen
    NAVER[:] = [(str(i), f"작품{i}", 1) for i in range(1000, 1000 + report_seen.MAX_ENTRIES + 50)]
    await report(); import json
    assert len(json.loads(repository.get_setting(report_seen.SETTING_KEY))) <= report_seen.MAX_ENTRIES
    print("7) 크기 제한 OK")
asyncio.run(main())
print("\n전부 통과")
