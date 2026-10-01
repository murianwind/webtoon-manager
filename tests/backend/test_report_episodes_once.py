"""다운로드 리포트: 받은/실패한 회차도 같은 회차는 한 번만 보고한다(같은 회차가 이력에 다시 쌓여도 반복 안 됨). 테스트 발송은 전부 보여주고 기록하지 않는다."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify, report_seen
db.get_connection()

sent = []
async def fake_send(session, settings, message): sent.append(message)
discord_notify.send_webhook_notification = fake_send
async def none(session, settings): return []
scheduler._collect_unregistered_new_episodes = none; scheduler._collect_kakao_new_episodes = none

async def report(force=False):
    sent.clear(); job_status.start("report"); await scheduler._run_report_job_impl(force_test=force); return list(sent)
def add(title_id, name, no, platform, status="success"): repository.add_episode_history(title_id, name, no, f"{no}화", status, "오류" if status == "failed" else "", platform=platform)

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.set_setting(scheduler._SETTING_KEY_REPORT_LAST_SENT_AT, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())

    # ── 1. 처음 받은 회차는 보고, 같은 회차가 이력에 다시 쌓여도(재다운로드/시각 꼬임 등) 반복하지 않는다 ──
    add("100", "네이버작", 5, "naver"); add("7", "카카오작", 410, "kakao"); add("7", "카카오작", 411, "kakao")
    msgs = await report(); assert len(msgs) == 1 and "네이버작" in msgs[0] and "카카오작 · 2개 회차(410~411번)" in msgs[0]
    add("100", "네이버작", 5, "naver"); add("7", "카카오작", 410, "kakao"); add("7", "카카오작", 411, "kakao")           # 같은 회차 이력이 다시 쌓임
    assert await report() == []                                                                                      # 보낼 게 없으면 메시지 자체를 안 보냄
    print("1) 같은 회차는 한 번만 OK")

    # ── 2. 새 회차가 하나라도 섞여 있으면 그것만 보고(이미 알린 회차는 빼고, 번호 범위/개수도 새 것만) ──
    add("7", "카카오작", 410, "kakao"); add("7", "카카오작", 412, "kakao"); add("100", "네이버작", 5, "naver")
    msgs = await report(); assert len(msgs) == 1 and "카카오작 · 1개 회차(412번)" in msgs[0] and "네이버작" not in msgs[0], msgs
    add("100", "네이버작", 6, "naver"); msgs = await report(); assert len(msgs) == 1 and "네이버작" in msgs[0] and "카카오작" not in msgs[0]
    print("2) 새 회차만 OK")

    # ── 3. 같은 번호라도 다른 작품/플랫폼이면 따로 센다 ──
    add("9", "카카오 다른작품", 5, "kakao"); add("8", "네이버 다른작품", 5, "naver")
    msgs = await report(); assert len(msgs) == 1 and "카카오 다른작품" in msgs[0] and "네이버 다른작품" in msgs[0]
    print("3) 작품/플랫폼 구분 OK")

    # ── 4. 실패: 같은 회차의 실패는 한 번만 알리고, 성공하면 알린 뒤 기록을 지워서 나중에 다시 실패하면 또 알린다 ──
    add("100", "네이버작", 7, "naver", "failed"); msgs = await report(); assert len(msgs) == 1 and "실패한 작품" in msgs[0] and "네이버작" in msgs[0]
    add("100", "네이버작", 7, "naver", "failed"); assert await report() == []                                         # 같은 실패 반복 안 함
    add("100", "네이버작", 7, "naver"); msgs = await report(); assert len(msgs) == 1 and "받은 작품" in msgs[0] and "네이버작" in msgs[0]   # 이번엔 성공 → 보고
    add("100", "네이버작", 7, "naver", "failed"); msgs = await report(); assert len(msgs) == 1 and "실패한 작품" in msgs[0]    # 성공 뒤에 다시 실패하면 새 문제라 다시 알림
    print("4) 실패 한 번만 OK")

    # ── 5. 테스트 발송: 이미 알린 것도 오늘 기록 전부를 보여주고, 기록은 남기지 않는다 ──
    msgs = await report(force=True); assert len(msgs) == 1 and "카카오작" in msgs[0] and "네이버 다른작품" in msgs[0]
    add("55", "테스트뒤신규", 1, "naver"); msgs = await report(force=True); assert "테스트뒤신규" in msgs[0]
    msgs = await report(); assert len(msgs) == 1 and "테스트뒤신규" in msgs[0]                                         # 테스트 발송이 기록하지 않았으니 정상 리포트에는 나온다
    print("5) 테스트 발송 OK")

    # ── 6. 전송이 실패하면 기록하지 않아서 다음에 다시 보낸다 ──
    async def boom(session, settings, message): raise RuntimeError("웹훅 오류")
    discord_notify.send_webhook_notification = boom; add("66", "전송실패작", 1, "naver"); sent.clear(); job_status.start("report"); await scheduler._run_report_job_impl()
    discord_notify.send_webhook_notification = fake_send; msgs = await report(); assert len(msgs) == 1 and "전송실패작" in msgs[0]
    print("6) 전송 실패 시 재시도 OK")

    # ── 7. 기억 목록은 무한히 커지지 않는다 ──
    rows = [{"platform": "naver", "title_id": str(i), "episode_no": 1, "status": "success"} for i in range(report_seen.MAX_ENTRIES + 20)]
    report_seen.remember_rows(rows); import json
    assert len(json.loads(repository.get_setting(report_seen.SETTING_KEY))) <= report_seen.MAX_ENTRIES
    print("7) 크기 제한 OK")
asyncio.run(main())
print("\n전부 통과")
