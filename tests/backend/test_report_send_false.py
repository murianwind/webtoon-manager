"""웹훅 전송이 실패하면 예외 대신 False가 돌아온다(디스코드가 204가 아닌 상태로 거절한 경우 등) — 이때도 실패로 보고 "알렸다"는 기록과 발송 기준 시각을 움직이지 않아서 다음에 다시 시도한다.
(다운로드 리포트와 웹툰 전체목록 알림 둘 다)"""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, discord_config, job_status, list_alert, report_seen, repository, scheduler, discord_notify
from fakes import discord_fake
db.get_connection()

sent = []
async def none(session, settings): return []
scheduler.report_sections.collect_unregistered_new_episodes = none; scheduler.report_sections.collect_kakao_new_episodes = none
scheduler.report_sections.collect_naver_new_titles = none; scheduler.report_sections.collect_kakao_new_titles = none
async def new_titles(session, settings): return [("12", "신규 네이버")]
LAST_KEY = scheduler._SETTING_KEY_REPORT_LAST_SENT_AT

def add(name, no): repository.add_episode_history("100", name, no, f"{no}화", "success", platform="naver")

async def main():
    discord_config.set_webhook_url("https://discord.test/hook")
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    repository.set_setting(LAST_KEY, since)

    # 1. 다운로드 리포트: False면 기록도 기준 시각도 그대로 → 다음 리포트에 다시 담긴다
    add("실패작", 1)
    discord_notify.send_webhook_notification = discord_fake(sent, result=False)
    job_status.start("report"); await scheduler.send_batch_report(datetime.now(timezone.utc).isoformat())
    assert repository.get_setting(LAST_KEY) == since, "전송이 실패했으니 발송 기준 시각을 옮기지 않는다"
    assert not report_seen.reportable_rows(repository.list_episode_history_window(since, datetime.now(timezone.utc).isoformat()))[0].get("redownload"), "알렸다고 기록하지 않았다"
    sent.clear(); discord_notify.send_webhook_notification = discord_fake(sent, result=True)
    job_status.start("report"); await scheduler.send_batch_report(datetime.now(timezone.utc).isoformat())
    assert len(sent) == 1 and "실패작" in sent[0] and "다시 받은 작품" not in sent[0], sent
    print("1) 다운로드 리포트 전송 실패(False) 재시도 OK")

    # 2. 웹툰 전체목록 알림: False면 알렸다는 기록도, "오늘 확인함"도 남기지 않는다 → 다시 시도
    scheduler.report_sections.collect_naver_new_titles = new_titles
    repository.set_setting(list_alert.SETTING_DONE, None); repository.set_setting(report_seen.SETTING_KEY, None)
    sent.clear(); discord_notify.send_webhook_notification = discord_fake(sent, result=False)
    now = datetime.now(timezone.utc); await scheduler.send_list_alert(now)
    assert not report_seen.is_new_announced("naver", "12"), "전송이 실패했으니 신규 웹툰을 알렸다고 기록하지 않는다"
    assert repository.get_setting(list_alert.SETTING_DONE) is None, "오늘 확인한 것으로 치지 않는다"
    sent.clear(); discord_notify.send_webhook_notification = discord_fake(sent, result=True)
    await scheduler.send_list_alert(now + timedelta(minutes=20))
    assert len(sent) == 1 and "신규 네이버" in sent[0] and report_seen.is_new_announced("naver", "12")
    print("2) 전체목록 알림 전송 실패(False) 재시도 OK")
asyncio.run(main())
print("\n전부 통과")
