"""디스코드 웹훅 알림 — 메시지 나누기와 발송(성공/실패/예외)과 설정 화면의 "웹훅 테스트".

디스코드는 한 메시지가 2000자를 넘거나 내용이 비어 있으면 거부한다. 그래서 길면 줄 단위로 나눠 보내고, 하나라도 못 보내면 False(알림 기록을 남기지 않게)로 돌려준다.
"""
import asyncio
import logging

import aiohttp

from app import db, discord_config, discord_notify, repository
db.get_connection()
from fakes import AsyncResponse

LIMIT = discord_notify.DISCORD_MESSAGE_CHUNK_LIMIT

# Scenario 1: 메시지 나누기 — 짧으면 그대로, 길면 줄 단위로 한도 안에서 나눈다. 빈 조각이 없고, 이어 붙이면 원문 그대로다. 한 줄이 한도보다 길면 그 줄을 잘라서 나눈다
assert discord_notify._split_message("짧은 글") == ["짧은 글"] and discord_notify._split_message("가" * LIMIT) == ["가" * LIMIT]
text = "\n".join(f"{i:04d} " + "나" * 95 for i in range(60))                              # 100자 안팎 줄 60개(약 6000자)
chunks = discord_notify._split_message(text)
assert len(chunks) >= 3 and all(0 < len(c) <= LIMIT for c in chunks), [len(c) for c in chunks]
assert "\n".join(chunks) == text, "줄을 빠뜨리거나 바꾸면 안 된다"
huge = "다" * (LIMIT * 2 + 50)                                                           # 줄바꿈 없는 아주 긴 한 줄
parts = discord_notify._split_message("앞 줄\n" + huge + "\n뒤 줄")
assert all(0 < len(p) <= LIMIT for p in parts) and "".join(parts).replace("\n", "") == ("앞 줄" + huge + "뒤 줄"), [len(p) for p in parts]   # 빈 조각 없음, 한도 초과 조각 없음, 내용 보존
lead = discord_notify._split_message(huge + "\n뒤 줄")                                                              # 긴 줄이 맨 앞에 와도 빈 조각이 생기지 않는다(빈 메시지는 디스코드가 거부한다)
assert all(0 < len(p) <= LIMIT for p in lead) and "".join(lead).replace("\n", "") == huge + "뒤 줄", [len(p) for p in lead]
assert discord_notify._split_message("\n".join(["라" * LIMIT] * 2)) == ["라" * LIMIT, "라" * LIMIT]                                  # 딱 한도인 줄 둘은 두 조각
print("1) 메시지 나누기 OK")


class Session:
    """post 호출을 기록하고, 지정한 응답(또는 예외)을 차례로 돌려주는 가짜 세션."""
    def __init__(self, *outcomes): self.outcomes, self.posts = list(outcomes), []
    def post(self, url, json=None, **kw):
        self.posts.append((url, json)); outcome = self.outcomes.pop(0) if self.outcomes else AsyncResponse(204)
        if isinstance(outcome, BaseException): raise outcome
        return outcome
send = lambda session, message: asyncio.run(discord_notify.send_webhook_notification(session, message))
class Logs(logging.Handler):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, record): self.lines.append(record.getMessage())
logs = Logs(); discord_notify.log.addHandler(logs)

# Scenario 2: 웹훅이 없거나 보낼 내용이 없으면 아무것도 보내지 않고 False
repository.set_setting(discord_config._KEY_WEBHOOK_URL, None)
s = Session(); assert send(s, "안녕") is False and s.posts == []
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/token")
for blank in ("", "   ", "\n\n"):
    s = Session(); assert send(s, blank) is False and s.posts == [], repr(blank)
print("2) 웹훅 없음/빈 메시지 OK")

# Scenario 3: 성공(204) — 웹훅 주소로 {"content": 조각}을 보내고 True. 길면 조각마다 차례로 보낸다
s = Session(); assert send(s, "안녕") is True and s.posts == [("https://discord.com/api/webhooks/1/token", {"content": "안녕"})]
s = Session(); assert send(s, text) is True and [p[1]["content"] for p in s.posts] == chunks and len(s.posts) == len(chunks)
print("3) 발송 성공 OK")

# Scenario 4: 실패 — 204가 아니면(상태와 본문을 로그에) False, 예외도 False. 한 조각이 실패해도 나머지 조각은 계속 보낸다
logs.lines.clear(); s = Session(AsyncResponse(400, text="Invalid Form Body")); assert send(s, "안녕") is False and any("400" in l and "Invalid Form Body" in l for l in logs.lines), logs.lines
logs.lines.clear(); s = Session(ConnectionError("연결 끊김")); assert send(s, "안녕") is False and any("연결 끊김" in l for l in logs.lines), logs.lines
s = Session(AsyncResponse(429, text="rate limited")); assert send(s, text) is False and len(s.posts) == len(chunks), "첫 조각이 실패해도 나머지를 보낸다"
s = Session(AsyncResponse(204), RuntimeError("두번째 실패")); assert send(s, text) is False and len(s.posts) == len(chunks)
print("4) 발송 실패 OK")

# Scenario 5: 설정 화면의 "웹훅 테스트" — 웹훅 없음 / 성공(204) / 실패(상태와 본문) / 예외를 사람이 읽을 문장으로
def test_message(outcome):
    session = Session(outcome)
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return session
        async def __aexit__(self, *a): return False
    real = aiohttp.ClientSession; aiohttp.ClientSession = Client
    try: return asyncio.run(discord_notify.send_test_webhook_message()), session
    finally: aiohttp.ClientSession = real
(ok, message), session = test_message(AsyncResponse(204)); assert ok and "전송했습니다" in message and session.posts[0][1]["content"].startswith("✅")
(ok, message), _ = test_message(AsyncResponse(404, text="Unknown Webhook")); assert not ok and "404" in message and "Unknown Webhook" in message
(ok, message), _ = test_message(ConnectionError("DNS 실패")); assert not ok and "DNS 실패" in message
repository.set_setting(discord_config._KEY_WEBHOOK_URL, None)
(ok, message), session = test_message(AsyncResponse(204)); assert not ok and "설정되지 않았습니다" in message and session.posts == []
print("5) 웹훅 테스트 OK")
print("ALL OK")
