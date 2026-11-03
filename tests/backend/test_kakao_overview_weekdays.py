"""카카오 작품 요일/판매 상태 — 실제 응답(HAR 캡처, tests/fixtures/kakao_series_states.json)으로 고정한다.

배경: 요일별 목록(캐시)에 없는 카카오 작품(연재 중인데 목록에 안 나오는 것, 판매 중지)이 "기타"에 남았다. 실제 응답으로 확인한 사실:
- 작품 개요(content/overview)의 content.pub_period가 연재 요일이다("목", "수", "월, 목" — 요일이 없는 작품은 필드 자체가 없다).
- 작품 정보의 state는 정상이면 ST61, 판매 중지는 ST62다(on_issue는 판매가 중지돼도 "Y" 그대로여서 연재 상태만으로는 구분이 안 된다).
"""
import asyncio
import json
from pathlib import Path

from app import db, kakao_page_download as kp, kakao_status
db.get_connection()
from fakes import AsyncResponse

REAL = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "kakao_series_states.json").read_text(encoding="utf-8"))

# Scenario 1: 연재 요일 읽기 — 실제 값("목", "수", "월, 목"), 요일 없음(None/빈 값), 알 수 없는 표기는 빈 목록(추측하지 않는다). 월~일 순서, 중복 제거
parse = kakao_status.parse_pub_period
assert parse(REAL["61658950"]["overview"]["pub_period"]) == ["thu"] and parse(REAL["61328034"]["overview"]["pub_period"]) == ["wed"] and parse(REAL["58672424"]["overview"]["pub_period"]) == ["mon", "thu"]
assert parse(REAL["66707967"]["overview"]["pub_period"]) == [] and parse("") == [] and parse(None) == []
assert parse("목, 월, 목") == ["mon", "thu"], "월~일 순서, 중복 제거"
for unknown in ("월~금", "매일", "격주 화", "화요일", "Tue"): assert parse(unknown) == [], f"추측하지 않는다: {unknown}"
print("1) 요일 읽기 OK")

# Scenario 2: 판매 상태 — ST62만 판매 중지(실제 사례 록사나), 정상(ST61)과 모르는 값/없음은 아니다. 판매 중지는 화면에서 가린다(완결/서비스 종료처럼)
assert kakao_status.is_sale_stopped("ST62") and not kakao_status.is_sale_stopped("ST61") and not kakao_status.is_sale_stopped(None) and not kakao_status.is_sale_stopped("ST99")
assert REAL["56434854"]["overview"]["state"] == "ST62" and all(REAL[k]["overview"]["state"] == "ST61" for k in ("61658950", "61328034", "58672424", "66707967")), "실제 값"
assert kakao_status.is_hidden({"on_issue": "Y", "http": 200, "state": "ST62"}) and not kakao_status.is_hidden({"on_issue": "Y", "http": 200, "state": "ST61"}), "연재 중(Y)이어도 판매 중지면 가린다"
print("2) 판매 상태 OK")

# Scenario 3: 개요 요청 — result.content를 돌려주고(요일 포함), 못 받으면 None(실패/내용 없음)
class Session:
    def __init__(self, outcome): self.outcome, self.calls = outcome, []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if isinstance(self.outcome, BaseException): raise self.outcome
        return self.outcome
overview = lambda outcome: asyncio.run(kp.KakaoPageClient(Session(outcome), {}, 5).fetch_overview(58672424))
content = {"title": "만고지존", "pub_period": "월, 목", "state": "ST61"}
s = Session(AsyncResponse(200, {"result_code": 0, "result": {"content": content}})); assert asyncio.run(kp.KakaoPageClient(s, {}, 5).fetch_overview(58672424)) == content
assert s.calls[0][0].endswith("/content/overview") and s.calls[0][1] == {"series_id": 58672424}, s.calls
assert overview(AsyncResponse(200, {"result_code": 0, "result": {}})) is None and overview(AsyncResponse(404)) is None and overview(AsyncResponse(200, {"result_code": 5})) is None and overview(ConnectionError("끊김")) is None
print("ALL OK")
