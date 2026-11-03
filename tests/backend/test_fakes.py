"""공용 가짜 코드(fakes.py)가 약속대로 동작하는지 — 이 가짜를 믿고 쓰는 테스트가 많아서 가짜 자체를 먼저 고정한다."""
import asyncio

from app import db, discord_config, discord_notify, repository
db.get_connection()
from fakes import AsyncResponse, capture_discord


async def main():
    # AsyncResponse — 컨텍스트 매니저로 쓰고, 지정한 값을 그대로 돌려준다
    async with AsyncResponse(201, {"a": 1}, text="본문", body=b"\x01\x02", headers={"X-A": "b"}, cookies={"sid": "v"}) as r:
        assert r.status == 201 and await r.json() == {"a": 1} and await r.json(content_type=None) == {"a": 1} and await r.text(errors="ignore") == "본문"
        assert await r.read() == b"\x01\x02" and r.headers == {"X-A": "b"} and r.cookies["sid"].value == "v"
    assert AsyncResponse().status == 200 and await AsyncResponse().json() is None and await AsyncResponse().read() == b""             # 기본값
    try:
        await AsyncResponse(200, bad_json=True).json(); raise AssertionError("bad_json이면 ValueError")
    except ValueError:
        pass
    r = AsyncResponse(); assert (await r.__aexit__(None, None, None)) is False                                                       # 예외를 삼키지 않는다
    print("1) AsyncResponse OK")

    # capture_discord — 보낸 메시지를 모으고 성공 값을 돌려준다
    sent = []; capture_discord(sent)
    assert await discord_notify.send_webhook_notification(None, "안녕") is True and sent == ["안녕"]
    sent2 = []; capture_discord(sent2, result=False); assert await discord_notify.send_webhook_notification(None, "x") is False and sent2 == ["x"]
    # require_webhook — 웹훅이 없으면 보내지 않은 것(False)으로, 있으면 보낸 것으로
    repository.set_setting(discord_config._KEY_WEBHOOK_URL, None)
    sent3 = []; capture_discord(sent3, require_webhook=True)
    assert await discord_notify.send_webhook_notification(None, "없음") is False and sent3 == []
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    assert await discord_notify.send_webhook_notification(None, "있음") is True and sent3 == ["있음"]
    # fail_when — 실패 상황 흉내
    state = {"fail": True}; sent4 = []; capture_discord(sent4, fail_when=lambda: state["fail"])
    try:
        await discord_notify.send_webhook_notification(None, "실패"); raise AssertionError("실패여야 한다")
    except RuntimeError:
        pass
    state["fail"] = False; await discord_notify.send_webhook_notification(None, "성공"); assert sent4 == ["성공"]
    print("2) capture_discord OK")
asyncio.run(main())
print("ALL OK")
