"""설정에 JSON 한 덩어리로 저장하는 상태의 공용 도우미(app/json_setting.py) — 리포트 발송 상태, 뷰어 스캔 상태, 요일/카카오 상태 조회 결과가 같은 방식으로 쓴다.
약속: ① 없거나 깨진 값은 기본값 ② 필드 일부만 바꿔도 나머지는 그대로 ③ 여러 스레드가 동시에 바꿔도 서로의 변경을 지우지 않는다(읽고-바꾸고-쓰기를 잠금으로 한 덩어리로) ④ 키마다 독립.
"""
from concurrent.futures import ThreadPoolExecutor

from app import db, repository
db.get_connection()
from app.json_setting import JsonSetting

# Scenario 1: 없으면 기본값(매번 새로 만든 사본 — 돌려받은 것을 바꿔도 기본값이 오염되지 않는다), 깨진 JSON/객체가 아닌 값도 기본값
state = JsonSetting("test_state_a", lambda: {"count": 0, "name": None})
assert state.load() == {"count": 0, "name": None}
got = state.load(); got["count"] = 99; assert state.load()["count"] == 0
for broken in ("이건 JSON이 아님", "[1, 2]", '"문자열"', "123"):
    repository.set_setting("test_state_a", broken); assert state.load() == {"count": 0, "name": None}, broken
repository.set_setting("test_state_a", None)
print("1) 기본값 OK")

# Scenario 2: 일부만 바꾸면 나머지는 그대로(저장된 값 위에 기본값을 채운다 — 나중에 필드가 늘어도 예전 저장값이 깨지지 않는다). 바꾼 결과를 돌려준다. 다시 불러도 그대로(저장됨)
assert state.update(count=3) == {"count": 3, "name": None}
assert state.update(name="가") == {"count": 3, "name": "가"} and state.load() == {"count": 3, "name": "가"}
repository.set_setting("test_state_a", '{"count": 7}'); assert state.load() == {"count": 7, "name": None}, "예전 저장값에 없는 필드는 기본값"
assert JsonSetting("test_state_a", lambda: {"count": 0, "name": None}).load()["count"] == 7, "다른 인스턴스(같은 키)도 같은 값"
state.clear(); assert state.load() == {"count": 0, "name": None} and repository.get_setting("test_state_a") is None
print("2) 일부 갱신/저장/지우기 OK")

# Scenario 3: 맵처럼 쓰기(기본값 {}) — edit(변경 함수)로 항목을 넣고 빼고, 한 번의 잠금 안에서 읽고-바꾸고-쓴다
store = JsonSetting("test_map_b")
assert store.load() == {}
store.edit(lambda data: data.update({"1": {"ok": True}})); store.edit(lambda data: data.update({"2": {"ok": False}}))
assert store.load() == {"1": {"ok": True}, "2": {"ok": False}}
store.edit(lambda data: data.pop("1")); assert store.load() == {"2": {"ok": False}}
print("3) 맵 사용 OK")

# Scenario 4: 동시에 바꿔도 서로의 변경을 지우지 않는다 — 8개 스레드로 120건(필드 갱신과 맵 항목 추가 모두). 보호하지 않으면 3분의 1 정도만 남는다
state.clear(); store.clear()
with ThreadPoolExecutor(8) as pool: list(pool.map(lambda i: state.update(**{f"k{i}": i}), range(120)))
assert all(f"k{i}" in state.load() for i in range(120)), sum(1 for i in range(120) if f"k{i}" in state.load())
with ThreadPoolExecutor(8) as pool: list(pool.map(lambda i: store.edit(lambda data, i=i: data.update({str(i): i})), range(120)))
assert len(store.load()) == 120, len(store.load())
print("4) 동시 변경 OK")

# Scenario 5: 키마다 독립 — 한 키를 바꿔도 다른 키는 그대로
other = JsonSetting("test_state_other", lambda: {"x": 1}); other.update(x=5); state.update(count=1); assert other.load() == {"x": 5} and state.load()["count"] == 1
print("ALL OK")
