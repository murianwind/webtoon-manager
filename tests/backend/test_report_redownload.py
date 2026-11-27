"""다운로드 리포트: 이미 알린 회차를 다시 받으면(웹툰이 수정돼서 재다운로드 등) "다시 받은 작품"으로 따로 알린다. 새 회차는 "받은 작품" 그대로, 같은 실패는 여전히 한 번만."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify
from fakes import discord_fake
db.get_connection()

sent = []
discord_notify.send_webhook_notification = discord_fake(sent, result=None)
async def none(session, settings): return []
scheduler.report_sections.collect_unregistered_new_episodes = none; scheduler.report_sections.collect_kakao_new_episodes = none
scheduler.report_sections.collect_naver_new_titles = none; scheduler.report_sections.collect_kakao_new_titles = none

async def report():
    sent.clear(); job_status.start("report"); await scheduler.send_batch_report(datetime.now(timezone.utc).isoformat()); return list(sent)
def add(title_id, name, no, platform, status="success"): repository.add_episode_history(title_id, name, no, f"{no}화", status, "오류" if status == "failed" else "", platform=platform)

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.set_setting(scheduler._SETTING_KEY_REPORT_LAST_SENT_AT, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())

    # 1. 처음 받으면 "받은 작품"만 — 다시 받은 작품 섹션은 없다
    add("7", "엔드라인", 30, "kakao"); add("100", "네이버작", 5, "naver")
    m = (await report())[0]; assert "받은 작품 (2)" in m and "다시 받은 작품" not in m, m

    # 2. 같은 회차를 다시 받으면 "다시 받은 작품"으로 알린다(받은 작품 섹션은 새 것이 없으니 생략)
    add("7", "엔드라인", 30, "kakao")
    msgs = await report(); assert len(msgs) == 1, msgs
    m = msgs[0]; assert "다시 받은 작품 (1)" in m and "엔드라인 · 1개 회차(30번)" in m and "받은 작품" not in m.replace("다시 받은 작품", ""), m

    # 3. 새 회차와 다시 받은 회차가 섞이면 두 섹션으로 나뉜다
    add("7", "엔드라인", 30, "kakao"); add("7", "엔드라인", 31, "kakao"); add("100", "네이버작", 5, "naver"); add("9", "새작품", 1, "naver")
    m = (await report())[0]
    head, again = m.split("다시 받은 작품")
    assert "받은 작품 (2)" in head and "엔드라인 · 1개 회차(31번)" in head and "새작품" in head and "네이버작" not in head, m
    assert "다시 받은 작품 (2)" in "다시 받은 작품" + again and "엔드라인 · 1개 회차(30번)" in again and "네이버작" in again and "새작품" not in again, m

    # 4. 다시 받은 것도 보고하고 나면 기록이 유지돼서, 그 뒤에 또 받으면 또 알린다(받을 때마다 한 번씩)
    add("100", "네이버작", 5, "naver"); assert "다시 받은 작품 (1)" in (await report())[0]
    assert await report() == []                                                                                      # 새로 쌓인 이력이 없으면 안 보낸다

    # 5. 같은 실패는 여전히 한 번만, 이미 알린 실패는 "다시 받은"에 안 들어간다
    add("100", "네이버작", 8, "naver", "failed"); assert "실패한 작품" in (await report())[0]
    add("100", "네이버작", 8, "naver", "failed"); assert await report() == []

    # 6. 전송이 실패하면 기록하지 않아서 다음 리포트에 다시 담긴다
    async def boom(session, message): raise RuntimeError("웹훅 오류")
    discord_notify.send_webhook_notification = boom; add("7", "엔드라인", 30, "kakao"); await report()
    discord_notify.send_webhook_notification = discord_fake(sent, result=None)
    assert "다시 받은 작품 (1)" in (await report())[0]
    print("재다운로드 알림 OK")
asyncio.run(main())
print("\n전부 통과")
