"""카카오페이지 대여 만료일 읽기 — 서버가 실제로 주는 형식(ISO 8601 "2026-10-12T01:46:22+09:00")로 만료를 판단한다.

예전 코드는 "2026-10-12 01:46:22" 형식만 읽고 못 읽으면 "아직 대여 중"으로 봐서, 만료된 기다무 대여(rent)가 영영 "보유(받을 수 있음)"로 보이고
받기에 실패하며 그 작품이 멈췄다. 이 테스트의 응답은 기다무.har(도굴왕: 엔드라인, 2026-10-13 캡처)에서 가져온 실제 응답이다.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from app import db
from app import kakao_page_download as kp
db.get_connection()

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "kakao_waitfree_reopen.json").read_text(encoding="utf-8"))
ITEMS = [entry["item"] for entry in FIXTURE["product_list"]["result"]["list"]]
SERIES_TITLE = FIXTURE["product_list"]["result"]["series_item"]["title"]


def item(expire, purchase_type="rent", free=False):
    return {"product_id": 1, "is_free": free, "service_property": {"purchase_info": {"purchase_type": purchase_type, "rent_expire_dt": expire}}}


# Scenario 1: 서버가 주는 ISO 형식 — 지난 만료일은 받을 수 없고, 남은 만료일은 받을 수 있다(예전 코드는 둘 다 "가능"이라고 읽었다)
now = datetime(2026, 10, 13, 16, 1)
assert kp._is_accessible(item("2026-10-12T01:46:22+09:00"), now) is False
assert kp._is_accessible(item("2026-10-16T16:01:59+09:00"), now) is True
# Scenario 2: 시간대는 한국 시각으로 바꿔서 비교한다(UTC로 와도 맞는다) — 16:01(KST) 기준 07:00Z(=16:00 KST)는 지났고 07:30Z(=16:30 KST)는 남았다
assert kp._is_accessible(item("2026-10-13T07:00:00+00:00"), now) is False and kp._is_accessible(item("2026-10-13T07:30:00Z"), now) is True
# Scenario 3: 예전 형식(초 있음/없음)도 그대로 읽는다
assert kp._is_accessible(item("2026-10-05 01:47:00"), now) is False and kp._is_accessible(item("2099-01-01 00:00:00"), now) is True
assert kp._is_accessible(item("2026-10-05 01:47"), now) is False and kp._is_accessible(item("2099-01-01 00:00"), now) is True
# Scenario 4: 만료일이 없으면(소장 등) 받을 수 있고, 무료는 항상 받을 수 있고, 살 수 없는 상태(not_purchased)는 받을 수 없다
assert kp._is_accessible(item(None), now) is True and kp._is_accessible(item(None, "own"), now) is True
assert kp._is_accessible(item("2026-10-05T01:47:25+09:00", free=True), now) is True
assert kp._is_accessible(item(None, "not_purchased"), now) is False
# Scenario 5: 읽을 수 없는 형식이면 "받을 수 없음"으로 보고(받기에 실패해서 작품이 멈추는 것보다 낫다) 경고를 남긴다 — 같은 모양의 값은 한 번만
records = []
class Capture(logging.Handler):
    def emit(self, record): records.append(record.getMessage())
logging.getLogger("app.kakao_page_download").addHandler(Capture()); logging.getLogger("app.kakao_page_download").setLevel(logging.WARNING)
kp._warned_expire_formats.clear()
assert kp._is_accessible(item("내일 오후"), now) is False and kp._is_accessible(item("내일 오후"), now) is False
assert len(records) == 1 and "내일 오후" in records[0] and "만료일" in records[0], records
print("1~5) 만료일 읽기 OK")

# Scenario 6: 실제 응답(기다무.har) — 2026-10-13 16:01 기준 대여 중으로 오던 14~22화(번호 15~23)가 모두 만료라 받을 수 없다. 무료 5화만 받을 수 있다
episodes = {ep.number: ep for ep in (kp._parse_episode(it, SERIES_TITLE, now) for it in ITEMS)}
expired = [n for n in range(15, 24)]
assert all(episodes[n].accessible is False and episodes[n].rent_expire is None for n in expired), {n: episodes[n].accessible for n in expired}
assert episodes[6].accessible is True and episodes[6].is_free is True
assert [n for n, ep in episodes.items() if ep.accessible] == [6]
# 만료 전 시점(2026-10-04 12:00)에는 14화(10-05 01:47 만료)와 16~22화(10-05 05:46~10-12)가 아직 대여 중이었다(15화는 10-03 21:54에 이미 만료)
before = {ep.number: ep for ep in (kp._parse_episode(it, SERIES_TITLE, datetime(2026, 10, 4, 12, 0)) for it in ITEMS)}
assert before[15].accessible is True and before[16].accessible is False and all(before[n].accessible for n in range(17, 24))
# Scenario 7: list_episodes도 같은 판단 — 실제 응답을 그대로 흘려보내면 만료된 대여가 받을 회차에 들어오지 않는다(이 만료일들은 모두 과거라 언제 돌려도 같다)
class FakeClient(kp.KakaoPageClient):
    def __init__(self): pass
    async def _request_json(self, *a, **k): return 200, {"result_code": 0, "result": {**FIXTURE["product_list"]["result"], "has_next": False}}
series_item, listed = asyncio.run(FakeClient().list_episodes(69500409))
assert [ep.number for ep in listed if ep.accessible] == [6] and series_item["title"] == SERIES_TITLE
# Scenario 8: 도굴왕 상황 — 폴더에 23번 파일만 있고 기록이 {6, 23}일 때, 받을 회차는 없고 기다무로 여는 첫 대상은 7번(15번으로 점프하지 않는다)
plan = kp.plan_by_folder_rules(listed, [kp.ExistingFile(number=23, name="0023_22화.zip", subtitle="22화")], SERIES_TITLE, {6, 23})
assert [e.number for e in plan.to_download] == [] and plan.locked[0].number == 7, (plan.to_download, plan.locked[:2])
print("6~8) 실제 응답(HAR)으로 만료된 대여를 받을 수 없다고 판단 OK")
print("ALL OK")
