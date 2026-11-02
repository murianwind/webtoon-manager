"""네이버 HTTP 가짜 서버 도우미(특성화 테스트용) — aiohttp.ClientSession.get(...)을 흉내 낸다.

FakeSession(handler): handler(url, params, attempt) → FakeResponse 또는 예외 객체. attempt는 같은 (url, params) 요청의 몇 번째 시도인지(0부터)라서
"처음엔 실패, 다시 하면 성공" 같은 재시도 시나리오를 만들 수 있다. 예외 객체를 돌려주면 `async with session.get(...)` 안에서 그 예외가 난다(네트워크 오류).
"""
import asyncio

_REAL_SLEEP = asyncio.sleep   # 테스트가 asyncio.sleep을 바꿔도(백오프 대기 기록용) 가짜 응답 안의 양보는 진짜로 한다


class InflightTracker:
    """동시에 진행 중인 이미지 받기의 수와 최댓값 — 동시 다운로드 상한 검증용.

    세는 구간은 응답을 받아서 쓰는 동안(요청의 async with 안) 전체다. 예전에는 파일을 쓰는 스레드(aiofiles) 타이밍에 기대서, 느린 CI 러너에서
    첫 이미지가 끝난 뒤에야 둘째가 시작해 "동시에 1개"로 재어지는 오탐이 있었다. rendezvous를 주면 그 수만큼 동시에 들어올 때까지 서로 기다린다 —
    상한이 지켜지면(세마포어) 정확히 그 수로 만나고, 상한이 낮으면 만나지 못해 시간 초과로 실패하므로 타이밍과 무관하게 결정적이다."""

    def __init__(self, rendezvous: int = 0, timeout: float = 0.5):
        self.current = 0
        self.max = 0
        self._rendezvous, self._timeout = rendezvous, timeout
        self._met = asyncio.Event()

    async def enter(self):
        self.current += 1
        self.max = max(self.max, self.current)
        if self._rendezvous and self.current >= self._rendezvous:
            self._met.set()
        if self._rendezvous and not self._met.is_set():
            try:
                await asyncio.wait_for(self._met.wait(), self._timeout)  # 동시에 들어오지 못하면(직렬화/상한 부족) 여기서 실패한다
            except BaseException:
                self.current -= 1  # 실패한 요청은 "진행 중"이 아니다 — 안 되돌리면 숫자가 쌓여서 실제로는 만나지 못했는데 만난 것으로 착각한다
                raise

    def leave(self):
        self.current -= 1


class _Content:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            await _REAL_SLEEP(0)
            yield chunk


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
        return _Content(self._chunks)


class _Request:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        tracker = getattr(self.outcome, "_tracker", None)
        if tracker:
            await tracker.enter()  # 응답을 받아 쓰는 동안(이 async with 안)이 "진행 중"이다
        return self.outcome

    async def __aexit__(self, *exc):
        tracker = getattr(self.outcome, "_tracker", None)
        if tracker and not isinstance(self.outcome, BaseException):
            tracker.leave()
        return False


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []   # calls: [(url, params, headers, cookies)]

    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        attempt = sum(1 for call in self.calls if call[0] == url and call[1] == params)
        self.calls.append((url, params, headers, cookies))
        return _Request(self.handler(url, params, attempt))
