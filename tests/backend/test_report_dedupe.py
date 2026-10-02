"""다운로드 리포트: 등록 안 한 작품의 "새 에피소드"는 같은 회차를 한 번만 알린다(하루에 여러 번 받아도 반복 안 됨)."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify
db.get_connection()

sent = []; FAIL = {"on": False}
async def fake_send(session, settings, message):
    if FAIL["on"]: raise RuntimeError("웹훅 전송 실패")
    sent.append(message)
discord_notify.send_webhook_notification = fake_send
NAVER = []; KAKAO = []
async def fake_naver(session, settings): return list(NAVER)
async def fake_kakao(session, settings): return list(KAKAO)
scheduler._collect_unregistered_new_episodes = fake_naver; scheduler._collect_kakao_new_episodes = fake_kakao

async def report(force=False):
    job_status.start("report"); before = len(sent)
    await scheduler._run_report_job_impl(force_test=force)
    return sent[before:], job_status.snapshot()["report"]["status"]

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.set_setting(scheduler._SETTING_KEY_REPORT_LAST_SENT_AT, (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    NAVER[:] = [("10", "네이버신작", 50)]; KAKAO[:] = [(7, "카카오신작", "https://page.kakao.com/content/7/viewer/700")]

    # ── 1. 처음엔 둘 다 알린다 ──
    msgs, st = await report()
    assert len(msgs) == 1 and "네이버신작" in msgs[0] and "카카오신작" in msgs[0] and st == "success"
    # ── 2. 같은 회차를 다시 받아도 반복하지 않는다(보낼 게 없으면 메시지 자체를 안 보냄) ──
    msgs, st = await report(); assert msgs == [] and st == "success"
    msgs, st = await report(); assert msgs == []
    print("1) 같은 새 에피소드는 한 번만 OK")

    # ── 3. 더 새로운 회차가 나오면 그 작품만 다시 알린다 ──
    NAVER[:] = [("10", "네이버신작", 51)]
    msgs, _ = await report(); assert len(msgs) == 1 and "네이버신작" in msgs[0] and "카카오신작" not in msgs[0], msgs
    KAKAO[:] = [(7, "카카오신작", "https://page.kakao.com/content/7/viewer/701")]
    msgs, _ = await report(); assert len(msgs) == 1 and "카카오신작" in msgs[0] and "네이버신작" not in msgs[0]
    KAKAO[:] = [(7, "카카오신작(제목만 바뀜)", "https://page.kakao.com/content/7/viewer/701")]      # 같은 회차 주소면 제목이 바뀌어도 반복 안 함
    msgs, _ = await report(); assert msgs == []
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

    # ── 6. 전송이 실패하면 "알렸다"고 기록하지 않아서 다음에 다시 시도한다 ──
    NAVER[:] = [("12", "실패테스트작", 9)]
    FAIL["on"] = True; msgs, st = await report(); assert msgs == [] and st == "error"
    FAIL["on"] = False; msgs, st = await report(); assert len(msgs) == 1 and "실패테스트작" in msgs[0] and st == "success"
    print("5) 전송 실패 시 재시도 OK")

    # ── 7. 테스트 발송은 이미 알린 것도 보여주고, 기록도 남기지 않는다 ──
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
