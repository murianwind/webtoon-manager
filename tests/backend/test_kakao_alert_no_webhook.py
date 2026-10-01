"""카카오 쿠키 알림 — 웹훅이 없으면 보낼 수 없으니 "보냈다"고 기록하지 않는다(나중에 웹훅을 달았을 때 알림이 하루 늦어지지 않게)."""
import asyncio

from app import db, discord_config, discord_notify, kakao_page_auth as auth, repository
db.get_connection()

sent = []
async def fake_send(session, settings, message):
    if discord_config.get_webhook_url(): sent.append(message)
discord_notify.send_webhook_notification = fake_send

async def main():
    # 웹훅 없음: 보내지 못하고, 기록도 남기지 않는다
    assert await auth.notify_if_needed(None, None, False, None) is False and sent == []
    assert repository.get_setting(auth._ALERT_EXPIRED_KEY) is None
    assert await auth.notify_if_needed(None, None, True, 3) is False and repository.get_setting(auth._ALERT_SOON_KEY) is None
    # 웹훅을 달면 바로 알림이 간다(하루 기다리지 않음) — 그리고 그때부터는 하루 한 번만
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    assert await auth.notify_if_needed(None, None, False, None) is True and len(sent) == 1 and repository.get_setting(auth._ALERT_EXPIRED_KEY)
    assert await auth.notify_if_needed(None, None, False, None) is False and len(sent) == 1
    print("카카오 쿠키 알림 OK (웹훅 없으면 기록 안 함, 생기면 바로 알림)")
asyncio.run(main())
print("\n전부 통과")
