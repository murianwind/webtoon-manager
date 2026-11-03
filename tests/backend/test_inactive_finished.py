"""제외됨/구독해제 네이버 작품의 완결 여부 저장 — 제외됨 화면이 완결 작품을 가리고(요일 조회에서도 뺀다) 하려면 DB의 is_finished가 채워져 있어야 한다.

예전 문제: is_finished는 "구독 중인 작품"의 신작 스캔에서만 저장됐다. 제외됨/구독해제 작품은 정보 조회(enrich_one)에서 완결 여부를 받아 오고도 저장하지 않아서,
완결 작품이 제외됨에 그대로 보이고 요일 조회도 완결 작품에 헛수고를 했다.
규칙: 구독 중이 아닌 작품은 정보를 받을 때마다 완결 여부를 그대로 따라 저장한다(다시 연재하면 해제). 구독 중인 작품은 기존대로 신작 스캔이 맡는다(구독하는 순간 완결 알림이 나가지 않게).
"""
import asyncio

import httpx

from app import db, naver_api, repository, tracker
from app.config import get_settings
from app.models import TitleInfo
db.get_connection()
import app.main as m

INFO = {}
def info(title_id, finished): return TitleInfo(title_id=title_id, title_name=f"작품{title_id}", synopsis="", is_adult=False, webtoon_type="webtoon", is_finished=finished, thumbnail_url="u")
async def fake_info(session, title_id, timeout): return INFO.get(title_id)
naver_api.fetch_title_info = fake_info

for tid, status in (("ex", repository.STATUS_EXCLUDED), ("un", repository.STATUS_UNSUBSCRIBED), ("ac", repository.STATUS_ACTIVE), ("back", repository.STATUS_EXCLUDED), ("down", repository.STATUS_EXCLUDED)):
    repository.upsert_new(tid, f"작품{tid}"); repository.set_status(tid, status)
repository.mark_finished("back")                                                                                                  # 예전에 완결로 저장됐지만 지금은 다시 연재 중인 작품
repository.set_finish_notified("back")
is_finished = lambda tid: repository.get(tid).is_finished
enrich = lambda tid: asyncio.run(tracker.enrich_one(None, tid, get_settings(), register_authors_enabled=False))

# Scenario 1: 제외됨/구독해제 작품은 정보를 받을 때 완결 여부를 저장한다. 다시 연재하면 해제한다. 알림 기록(finish_notified)은 건드리지 않는다
INFO.update(ex=info("ex", True), un=info("un", True), back=info("back", False), down=info("down", False))
assert enrich("ex")[0] and is_finished("ex") is True
assert enrich("un")[0] and is_finished("un") is True
assert enrich("back")[0] and is_finished("back") is False and repository.get("back").finish_notified is True, "해제해도 알림 기록은 그대로"
assert enrich("down")[0] and is_finished("down") is False
print("1) 완결 저장/해제 OK")

# Scenario 2: 구독 중인 작품은 enrich_one이 완결을 저장하지 않는다(구독하는 순간 완결 알림이 나가지 않게 — 기존처럼 신작 스캔이 맡는다)
INFO["ac"] = info("ac", True); assert enrich("ac")[0] and is_finished("ac") is False
print("2) 구독 중 작품은 그대로 OK")

# Scenario 3: 정보를 못 받으면(None) 완결 여부를 바꾸지 않는다
INFO["ex"] = None; repository.mark_finished("down"); assert enrich("ex")[0] is False and is_finished("ex") is True and is_finished("down") is True
print("3) 조회 실패 시 유지 OK")

# Scenario 4: 신작 스캔의 "구독해제/제외됨 정보 갱신"(refresh_inactive_metadata)이 같은 규칙으로 갱신하고, 제외됨 목록 API가 is_finished를 돌려준다
INFO.update(ex=info("ex", False), un=info("un", False), back=info("back", True), down=info("down", True), ac=info("ac", True))
refreshed = asyncio.run(tracker.refresh_inactive_metadata(None, get_settings())); assert refreshed == 4, refreshed
assert (is_finished("ex"), is_finished("un"), is_finished("back"), is_finished("down"), is_finished("ac")) == (False, False, True, True, False)
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        rows = {w["title_id"]: w for w in (await c.get("/api/webtoons", params={"status": "excluded"})).json()}
        assert rows["back"]["is_finished"] is True and rows["ex"]["is_finished"] is False
asyncio.run(api())
print("4) 정기 갱신/API OK")
print("ALL OK")
