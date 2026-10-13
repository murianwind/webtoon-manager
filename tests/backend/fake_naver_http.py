"""네이버 HTTP 가짜 서버 도우미(특성화 테스트용) — aiohttp.ClientSession.get(...)을 흉내 낸다.

FakeSession(handler): handler(url, params, attempt) → FakeResponse 또는 예외 객체. attempt는 같은 (url, params) 요청의 몇 번째 시도인지(0부터)라서
"처음엔 실패, 다시 하면 성공" 같은 재시도 시나리오를 만들 수 있다. 예외 객체를 돌려주면 `async with session.get(...)` 안에서 그 예외가 난다(네트워크 오류).
"""
import asyncio

_REAL_SLEEP = asyncio.sleep   # 테스트가 asyncio.sleep을 바꿔도(백오프 대기 기록용) 가짜 응답 안의 양보는 진짜로 한다


class InflightTracker:
    """동시에 진행 중인 이미지 받기의 수와 최댓값 — 동시 다운로드 상한 검증용."""

    def __init__(self):
        self.current = 0
        self.max = 0

    def enter(self):
        self.current += 1
        self.max = max(self.max, self.current)

    def leave(self):
        self.current -= 1


class _Content:
    def __init__(self, chunks, tracker):
        self._chunks, self._tracker = chunks, tracker

    async def iter_chunked(self, size):
        if self._tracker:
            self._tracker.enter()
        try:
            for chunk in self._chunks:
                await _REAL_SLEEP(0)
                yield chunk
        finally:
            if self._tracker:
                self._tracker.leave()


class FakeResponse:
    def __init__(self, status=200, json_data=None, text="", headers=None, chunks=(b"data",), tracker=None):
        self.status, self.headers = status, dict(headers or {})
        self._json, self._text, self._chunks, self._tracker = json_data, text, list(chunks), tracker

    async def json(self, *args, **kwargs):
        return self._json

    async def text(self, *args, **kwargs):
        return self._text

    @property
    def content(self):
        return _Content(self._chunks, self._tracker)


class _Request:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []   # calls: [(url, params, headers, cookies)]

    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        attempt = sum(1 for call in self.calls if call[0] == url and call[1] == params)
        self.calls.append((url, params, headers, cookies))
        return _Request(self.handler(url, params, attempt))
