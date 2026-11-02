"""다운로드 리포트의 "발송하지 않는 시간" — 시각 구간(시작~끝) 여러 개, 구간마다 요일(시작하는 날 기준, 자정을 넘길 수 있음), 겹침 검사.

- 요일을 안 고르면 매일. 끝 시각은 포함하지 않는다(06:00에 끝나는 구간은 06:00부터 발송 가능).
- 겹치는 구간은 저장할 때 오류로 알린다(한 주 안의 실제 시간으로 비교 — 자정/주를 넘기는 경우 포함).
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app import db, repository, report_dispatch as rd
db.get_connection()
import app.main as m

KST, UTC = ZoneInfo("Asia/Seoul"), timezone.utc
q = lambda sh, sm, eh, em, days=(): {"start_hour": sh, "start_minute": sm, "end_hour": eh, "end_minute": em, "days": list(days)}
def bad(items, needle):
    try: rd.parse_quiet_hours(items)
    except ValueError as e: assert needle in str(e), (needle, str(e)); return str(e)
    raise AssertionError(f"오류가 나야 한다: {items}")

# Scenario 1: 정리 — 요일은 월~일 순서로 정리하고 값은 그대로 둔다. 빈 목록(=항상 발송)도 유효
assert rd.parse_quiet_hours([]) == []
got = rd.parse_quiet_hours([q(23, 0, 6, 0, ["fri", "mon", "mon"])])
assert got == [q(23, 0, 6, 0, ["mon", "fri"])], got
# Scenario 2: 잘못된 값 — 시작과 끝이 같음, 범위 밖 시각, 모르는 요일
bad([q(9, 0, 9, 0)], "같"); bad([q(24, 0, 6, 0)], "시"); bad([q(9, 61, 10, 0)], "분"); bad([q(9, 0, 10, 0, ["someday"])], "요일")
print("1~2) 정리/검증 OK")

# Scenario 3: 겹침 — 한 주 안의 실제 시간으로 비교(끝 시각은 제외). 메시지에 두 구간(시각과 요일)이 나온다
msg = bad([q(23, 0, 6, 0), q(5, 0, 7, 0, ["mon"])], "겹칩니다"); assert "23:00~06:00" in msg and "05:00~07:00" in msg and "월" in msg, msg           # 매일 23~06과 월 05~07
rd.parse_quiet_hours([q(23, 0, 6, 0, ["mon"]), q(6, 0, 8, 0, ["mon"])])                                                      # 06:00에 끝나고 06:00에 시작 — 맞닿기만 하면 겹치지 않는다
bad([q(23, 0, 6, 0, ["mon"]), q(2, 0, 4, 0, ["tue"])], "겹칩니다")                                                            # 월 23시 → 화 06시 안에 화 02~04가 들어 있다(자정을 넘김)
rd.parse_quiet_hours([q(23, 0, 6, 0, ["mon"]), q(2, 0, 4, 0, ["mon"])])                                                       # 월 02~04는 월요일 23시에 시작하는 구간 밖이다
bad([q(23, 0, 2, 0, ["sun"]), q(1, 0, 3, 0, ["mon"])], "겹칩니다")                                                            # 일 23시 → 월 02시(주를 넘김)와 월 01~03
rd.parse_quiet_hours([q(9, 0, 12, 0, ["mon", "wed"]), q(9, 0, 12, 0, ["tue"])])                                               # 요일이 다르면 같은 시각이어도 된다
bad([q(9, 0, 12, 0), q(11, 0, 13, 0, ["sat"])], "겹칩니다")                                                                    # 한쪽이 매일이면 그 요일에 겹친다
print("3) 겹침 검사 OK")

# Scenario 4: 지금이 발송하지 않는 시간인가 — 요일은 "시작하는 날" 기준. 2026-10-12는 월요일(한국시간)
set_ = lambda items: repository.set_setting(rd.QUIET_KEY, __import__("json").dumps(items))
set_([q(23, 0, 6, 0)])
at = lambda y, mo, d, h, mi: datetime(y, mo, d, h, mi, tzinfo=KST)
assert rd.is_quiet(at(2026, 10, 12, 23, 30)) and rd.is_quiet(at(2026, 10, 13, 0, 0)) and rd.is_quiet(at(2026, 10, 13, 5, 59))
assert not rd.is_quiet(at(2026, 10, 13, 6, 0)) and not rd.is_quiet(at(2026, 10, 12, 22, 59)) and not rd.is_quiet(at(2026, 10, 12, 12, 0))       # 끝 시각 06:00부터는 발송 가능
assert rd.is_quiet(datetime(2026, 10, 12, 14, 30, tzinfo=UTC))                                                                                 # UTC로 들어와도 한국시간(월 23:30)으로 본다
set_([q(23, 0, 6, 0, ["mon"])])
assert rd.is_quiet(at(2026, 10, 12, 23, 30)) and rd.is_quiet(at(2026, 10, 13, 3, 0))                                                            # 월 23시에 시작해 화 새벽까지
assert not rd.is_quiet(at(2026, 10, 12, 3, 0)) and not rd.is_quiet(at(2026, 10, 18, 23, 30)) and not rd.is_quiet(at(2026, 10, 14, 3, 0))      # 월 새벽/일 밤/수 새벽은 아니다
set_([q(23, 0, 2, 0, ["sun"])]); assert rd.is_quiet(at(2026, 10, 18, 23, 30)) and rd.is_quiet(at(2026, 10, 19, 1, 0)) and not rd.is_quiet(at(2026, 10, 19, 2, 0))   # 일 → 월(주를 넘김)
set_([q(9, 0, 10, 0), q(14, 0, 15, 0, ["sat"])]); assert rd.is_quiet(at(2026, 10, 12, 9, 30)) and not rd.is_quiet(at(2026, 10, 12, 14, 30)) and rd.is_quiet(at(2026, 10, 17, 14, 30))
set_([]); assert not rd.is_quiet(at(2026, 10, 12, 3, 0))                                                                                         # 지정이 없으면 항상 발송
print("4) 발송하지 않는 시간 판정 OK")

# Scenario 5: 저장 — 잘못된 값이면 기존 값을 그대로 둔다. 모으는 시간(N분)은 0~1440, 기본 10
rd.set_quiet_hours([q(1, 0, 2, 0)]); assert rd.get_quiet_hours() == [q(1, 0, 2, 0)]
try: rd.set_quiet_hours([q(1, 0, 1, 0)])
except ValueError: pass
assert rd.get_quiet_hours() == [q(1, 0, 2, 0)]
repository.set_setting(rd.BATCH_KEY, None); assert rd.get_batch_minutes() == 10
rd.set_batch_minutes(0); assert rd.get_batch_minutes() == 0
for wrong in (-1, 1441):
    try: rd.set_batch_minutes(wrong); raise AssertionError("범위 밖은 거부")
    except ValueError: pass
repository.set_setting(rd.QUIET_KEY, "이건 JSON이 아님"); assert rd.get_quiet_hours() == []                                                      # 깨진 값은 빈 목록
print("5) 저장 OK")


async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        repository.set_setting(rd.QUIET_KEY, None); repository.set_setting(rd.BATCH_KEY, None)
        assert (await c.get("/api/settings/report")).json() == {"quiet_hours": [], "batch_minutes": 10}
        body = {"quiet_hours": [q(23, 0, 6, 0, ["mon", "tue"])], "batch_minutes": 15}
        assert (await c.post("/api/settings/report", json=body)).json() == body and (await c.get("/api/settings/report")).json() == body
        r = await c.post("/api/settings/report", json={"quiet_hours": [q(23, 0, 6, 0), q(5, 0, 7, 0, ["mon"])], "batch_minutes": 15})
        assert r.status_code == 422 and "겹칩니다" in r.json()["detail"], r.text                                                        # 겹침은 오류 메시지로 알린다
        assert (await c.get("/api/settings/report")).json() == body                                                                      # 거부되면 저장 안 됨
        assert (await c.post("/api/settings/report", json={"quiet_hours": [], "batch_minutes": -5})).status_code == 422
        assert (await c.post("/api/settings/report", json={"quiet_hours": [q(9, 0, 9, 0)], "batch_minutes": 5})).status_code == 422
asyncio.run(api())
print("6) API OK")
print("ALL OK")
