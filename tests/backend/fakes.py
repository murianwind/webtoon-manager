"""백엔드 테스트 공용 가짜 코드 — 테스트마다 복사하던 것을 한 곳에 모았다.

- AsyncResponse: `async with session.get(...) as resp:`로 쓰는 aiohttp 응답 흉내(status/json/text/read/headers/cookies).
- capture_discord(sent, ...): 디스코드 웹훅 발송(discord_notify.send_webhook_notification)을 가로채서 보낸 메시지를 sent 목록에 모은다.
  (세션/요청 기록 방식이 테스트마다 달라서 가짜 세션은 각 테스트가 직접 만들고, 응답 객체만 공유한다.)
"""
from http.cookies import SimpleCookie

from app import discord_config, discord_notify


class AsyncResponse:
    """aiohttp 응답 흉내. status, json 데이터(data), 문자열 본문(text), 바이트 본문(body), 헤더, 쿠키를 지정한다.
    bad_json=True면 json()이 JSON이 아닌 응답처럼 ValueError를 낸다."""

    def __init__(self, status=200, data=None, *, text="", body=b"", headers=None, cookies=None, bad_json=False):
        self.status, self._data, self._text, self._body, self._bad_json = status, data, text, body, bad_json
        self.headers = dict(headers or {})
        self.cookies = SimpleCookie()
        for name, value in (cookies or {}).items():
            self.cookies[name] = value

    async def json(self, *args, **kwargs):
        if self._bad_json:
            raise ValueError("not json")
        return self._data

    async def text(self, *args, **kwargs):
        return self._text

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def discord_fake(sent, *, result=True, require_webhook=False, fail_when=None):
    """디스코드 웹훅 발송을 흉내 내는 함수를 만들어 돌려준다 — 보낸 메시지를 sent 목록에 추가한다(설치는 호출하는 쪽이: `discord_notify.send_webhook_notification = ...`).
    result: 발송 성공 여부로 돌려줄 값(기본 True, 코드가 돌려받은 값을 안 쓰던 예전 가짜와 같게 하려면 None).
    require_webhook=True면 웹훅이 설정돼 있을 때만 보낸 것으로 센다(없으면 False를 돌려주고 기록하지 않음).
    fail_when: 호출할 때마다 불러서 True면 발송 실패(RuntimeError)를 낸다(실패 상황을 흉내 낼 때)."""
    async def fake_send(session, message):
        if fail_when is not None and fail_when():
            raise RuntimeError("웹훅 전송 실패")
        if require_webhook and not discord_config.get_webhook_url():
            return False
        sent.append(message)
        return result

    return fake_send


def capture_discord(sent, **options):
    """discord_fake를 만들어 discord_notify.send_webhook_notification 자리에 바로 설치한다(다른 모듈이 이름을 따로 가져다 썼다면 그 모듈에는 직접 설치)."""
    fake_send = discord_fake(sent, **options)
    discord_notify.send_webhook_notification = fake_send
    return fake_send
