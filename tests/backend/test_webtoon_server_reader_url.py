"""뷰어 서버의 "바로가기" 링크 조회(fetch_reader_url) — 리포트 메시지를 보강하는 부가 정보라서, 어떤 실패도 예외가 아니라 None이어야 한다(실패해도 리포트는 계속 나가야 한다)."""
import asyncio
import logging

from app import webtoon_server_client as client
from fakes import AsyncResponse


class Session:
    def __init__(self, outcome=None): self.outcome, self.calls = outcome, []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout.total))
        if isinstance(self.outcome, BaseException): raise self.outcome
        return self.outcome
fetch = lambda session, base="http://viewer", name="작품", timeout=7: asyncio.run(client.fetch_reader_url(session, base, name, timeout))
class Logs(logging.Handler):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, record): self.lines.append(record.getMessage())
logs = Logs(); client.log.addHandler(logs)

# Scenario 1: 주소가 없으면 요청하지 않고 None
s = Session(AsyncResponse(200, {"url": "x"})); assert fetch(s, base="") is None and s.calls == []

# Scenario 2: 성공 — 뷰어 주소(끝 슬래시 정리)의 /api/lookup/latest에 작품 이름을 series로, 제한 시간과 함께 요청하고 url을 돌려준다
s = Session(AsyncResponse(200, {"url": "http://viewer/series/1"}))
assert fetch(s, base="http://viewer/", name="쌍갑포차") == "http://viewer/series/1"
assert s.calls == [("http://viewer/api/lookup/latest", {"series": "쌍갑포차"}, 7)], s.calls

# Scenario 3: 실패는 모두 None — HTTP 오류, url이 없거나 비어 있음, JSON이 아님, 연결 오류(경고 로그만 남기고 예외는 던지지 않는다)
assert fetch(Session(AsyncResponse(404, {"url": "x"}))) is None and fetch(Session(AsyncResponse(500))) is None
assert fetch(Session(AsyncResponse(200, {}))) is None and fetch(Session(AsyncResponse(200, {"url": ""}))) is None and fetch(Session(AsyncResponse(200, {"url": None}))) is None
logs.lines.clear(); assert fetch(Session(AsyncResponse(200, bad_json=True)), name="이상한응답") is None and any("이상한응답" in l for l in logs.lines), logs.lines
logs.lines.clear(); assert fetch(Session(ConnectionError("연결 끊김"))) is None and any("연결 끊김" in l for l in logs.lines), logs.lines
assert fetch(Session(asyncio.TimeoutError())) is None
print("ALL OK")
