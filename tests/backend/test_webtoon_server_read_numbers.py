"""webtoon_server_client.fetch_read_numbers — 뷰어의 /api/lookup/read-state로 읽은 회차 번호 집합을 얻는다. 어떤 실패든 None."""
import asyncio

from app import webtoon_server_client as client


class FakeResponse:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status, self._body, self._bad = status, body, bad_json
    async def json(self):
        if self._bad: raise ValueError("not json")
        return self._body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if self.error: raise self.error
        return self.response


def run(session, base="http://viewer:8000/", name="작품"):
    return asyncio.run(client.fetch_read_numbers(session, base, name, 10))


# Given 뷰어가 읽은 회차 [1,2,5]를 돌려줌 / When 조회 / Then 집합으로 받고, 올바른 경로와 파라미터로 호출
s = FakeSession(FakeResponse(200, {"series_id": "x", "read_numbers": [1, 2, 5]}))
assert run(s) == frozenset({1, 2, 5}) and s.calls == [("http://viewer:8000/api/lookup/read-state", {"series": "작품"})], s.calls
print("1) 정상 응답 OK")

# 읽은 회차가 하나도 없으면 빈 집합(None이 아님 — 전부 안 읽음이라는 확실한 답)
assert run(FakeSession(FakeResponse(200, {"series_id": "x", "read_numbers": []}))) == frozenset()
print("2) 빈 목록은 빈 집합 OK")

# 실패는 전부 None: 주소 미설정, 404, 형식 오류, 숫자 아님, 타임아웃/연결 오류
assert run(FakeSession(FakeResponse()), base="") is None
assert run(FakeSession(FakeResponse(404, {}))) is None
assert run(FakeSession(FakeResponse(200, bad_json=True))) is None
assert run(FakeSession(FakeResponse(200, {"series_id": "x"}))) is None
assert run(FakeSession(FakeResponse(200, {"read_numbers": ["a"]}))) is None
assert run(FakeSession(error=asyncio.TimeoutError())) is None
print("3) 실패는 모두 None OK")

# 스케줄러 연결: 아카이빙 스레드(동기)에서 부르는 조회 함수가 이벤트 루프의 비동기 조회를 거쳐 결과를 돌려주고, 제목은 폴더명 규칙(금지문자 치환)으로 바꿔 묻는다
from app.scheduler.archive_job import _build_read_numbers_provider

async def via_thread():
    s = FakeSession(FakeResponse(200, {"read_numbers": [3]}))
    provider = _build_read_numbers_provider(asyncio.get_running_loop(), s, "http://viewer:8000", 10)
    naver = await asyncio.to_thread(provider, "제목 : 부제", False)
    kakao = await asyncio.to_thread(provider, "제목 : 부제", True)
    return naver, kakao, [c[1]["series"] for c in s.calls]
naver, kakao, asked = asyncio.run(via_thread())
assert naver == kakao == frozenset({3}) and asked == ["제목 ： 부제", "제목 _ 부제"], asked
print("4) 스케줄러 연결 OK (스레드→루프 조회, 네이버/카카오 폴더명 규칙)")

print("\n전부 통과")
