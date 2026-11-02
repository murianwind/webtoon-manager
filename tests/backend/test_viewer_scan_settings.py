"""뷰어 서버 스캔 연동 — 클라이언트(스캔 시작/상태, 인증 헤더, 결과 해석)와 설정 API(토큰은 암호화해서 저장하고 값을 돌려주지 않는다).

시작 요청은 스캔이 15~30분 걸려도 바로 돌아오고, 이후 상태만 확인한다. 연동이 없거나(404) 토큰이 안 맞으면(401/403) "unsupported"라 스캔은 건너뛴다.
"""
import asyncio

import httpx

from app import db, repository, viewer_scan, webtoon_server_client as client
db.get_connection()
import app.main as m


class Resp:
    def __init__(self, status=200, data=None): self.status, self._data = status, data
    async def json(self, content_type=None): return self._data
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
class Session:
    def __init__(self, outcome): self.outcome, self.calls = outcome, []
    def _call(self, method, url, headers, timeout):
        self.calls.append((method, url, headers, timeout.total))
        if isinstance(self.outcome, BaseException): raise self.outcome
        return self.outcome
    def post(self, url, headers=None, timeout=None): return self._call("POST", url, headers, timeout)
    def get(self, url, headers=None, timeout=None): return self._call("GET", url, headers, timeout)
run = lambda coro: asyncio.run(coro)

# Scenario 1: 스캔 시작 — 인증 헤더와 주소(끝 슬래시 정리), 응답 해석
for status, data, expected in ((200, {"started": True}, "started"), (200, {"started": False, "running": True}, "running"), (404, None, "unsupported"), (401, None, "unsupported"), (403, None, "unsupported"), (500, None, "error")):
    s = Session(Resp(status, data)); assert run(client.start_scan(s, "http://viewer/", "토큰", 7)) == expected, (status, expected)
assert s.calls == [("POST", "http://viewer/api/integration/rescan", {"X-Integration-Token": "토큰"}, 7)], s.calls
assert run(client.start_scan(Session(ConnectionError("연결 안 됨")), "http://viewer", "t", 5)) == "error"                      # 연결 오류도 "error" — 리포트는 그대로 간다
# Scenario 2: 스캔 상태 — running이 들어 있는 응답만 인정, 못 읽으면 None(다음 확인 때 다시)
assert run(client.scan_status(Session(Resp(200, {"running": True, "last_scan_at": "x"})), "http://viewer", "t", 5)) == {"running": True, "last_scan_at": "x"}
assert run(client.scan_status(Session(Resp(200, {"running": False})), "http://viewer", "t", 5)) == {"running": False}
for bad in (Resp(200, {"nothing": 1}), Resp(200, ["x"]), Resp(500, None), Resp(404, None), ConnectionError("끊김")):
    assert run(client.scan_status(Session(bad), "http://viewer", "t", 5)) is None
s = Session(Resp(200, {"running": False})); run(client.scan_status(s, "http://viewer/", "토큰", 3)); assert s.calls == [("GET", "http://viewer/api/integration/scan-status", {"X-Integration-Token": "토큰"}, 3)]
print("1~2) 클라이언트 OK")

async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        for key in (viewer_scan.ENABLED_KEY, viewer_scan.TOKEN_KEY, viewer_scan.MAX_WAIT_KEY, "webtoon_server_url"): repository.set_setting(key, None)
        # Scenario 3: 기본값 — 꺼짐, 토큰 없음, 최대 대기 45분
        assert (await c.get("/api/settings/viewer-scan")).json() == {"enabled": False, "token_set": False, "max_wait_minutes": 45, "ready": False}
        # Scenario 4: 켜고 토큰/대기 시간 저장 — 토큰 값은 응답에도 목록에도 나오지 않고 DB에는 암호화돼 있다. 뷰어 서버 주소가 있어야 ready
        r = (await c.post("/api/settings/viewer-scan", json={"enabled": True, "token": "secret-token", "max_wait_minutes": 60})).json()
        assert r == {"enabled": True, "token_set": True, "max_wait_minutes": 60, "ready": False} and "secret-token" not in str(r) and "secret-token" not in (await c.get("/api/settings/viewer-scan")).text
        assert repository.get_setting(viewer_scan.TOKEN_KEY) != "secret-token" and viewer_scan.get_token() == "secret-token"
        repository.set_setting("webtoon_server_url", "http://viewer"); assert (await c.get("/api/settings/viewer-scan")).json()["ready"] is True and viewer_scan.is_ready()
        # Scenario 5: 토큰을 안 보내면(또는 비워 두면) 기존 토큰 유지, clear_token이면 지움(그러면 ready가 아님)
        assert (await c.post("/api/settings/viewer-scan", json={"enabled": True, "max_wait_minutes": 30})).json()["token_set"] is True and viewer_scan.get_token() == "secret-token"
        assert (await c.post("/api/settings/viewer-scan", json={"enabled": True, "token": "", "max_wait_minutes": 30})).json()["token_set"] is True
        r = (await c.post("/api/settings/viewer-scan", json={"enabled": True, "clear_token": True, "max_wait_minutes": 30})).json()
        assert r["token_set"] is False and r["ready"] is False and viewer_scan.get_token() == ""
        # 토큰은 HTTP 헤더로 보내므로 영문/숫자/기호(ASCII)만 — 한글 등은 저장 전에 알려 준다(저장되지 않음)
        r = await c.post("/api/settings/viewer-scan", json={"enabled": True, "token": "한글토큰", "max_wait_minutes": 30}); assert r.status_code == 422 and "영문" in r.json()["detail"]
        assert viewer_scan.get_token() == ""
        # Scenario 6: 최대 대기는 1~240분, 끄면 ready가 아님
        for wrong in (0, 241, -3): assert (await c.post("/api/settings/viewer-scan", json={"enabled": True, "max_wait_minutes": wrong})).status_code == 422
        assert (await c.get("/api/settings/viewer-scan")).json()["max_wait_minutes"] == 30                                          # 거부되면 저장 안 됨
        await c.post("/api/settings/viewer-scan", json={"enabled": True, "token": "t", "max_wait_minutes": 30}); assert viewer_scan.is_ready()
        assert (await c.post("/api/settings/viewer-scan", json={"enabled": False, "max_wait_minutes": 30})).json()["ready"] is False
asyncio.run(api())
print("3~6) 설정 API OK")
print("ALL OK")
