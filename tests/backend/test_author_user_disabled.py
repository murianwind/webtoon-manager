"""사용자가 직접 끈 작가만 자동 등록에서 지킨다 — 앱이 후보로 채워 둔 꺼진 작가(구독해제/제외됨/목록 웹툰의 작가)는 그 작가의 작품을 구독하면 켜진다.
직접 끈 것 = "작가/태그 관리"에서 칩을 눌러 뺀 것. 다시 켜거나 직접 추가하면 그 보호는 풀린다. 백업/복원에도 이어진다."""
import asyncio

import httpx

from app import db, repository
db.get_connection()
import app.main as m

AUTO = repository.AUTHOR_ENABLED
KAKAO = "kakao"


def state(author_id, platform="naver"):
    return {a.author_id: a.enabled for a in repository.list_watched_authors(platform)}.get(author_id)


# ── 1. 앱이 후보로 채운 꺼진 작가는 구독(켜는 요청)을 받으면 켜진다 ──
assert repository.upsert_watched_author("후보작가", "후보작가", False, KAKAO) == repository.AUTHOR_ADDED and state("후보작가", KAKAO) is False
assert repository.upsert_watched_author("후보작가", "후보작가", False, KAKAO) == repository.AUTHOR_ALREADY_OFF and state("후보작가", KAKAO) is False   # 후보를 또 채워도 그대로 꺼짐
assert repository.upsert_watched_author("후보작가", "후보작가", True, KAKAO) == AUTO and state("후보작가", KAKAO) is True
assert repository.upsert_watched_author("후보작가", "후보작가", True, KAKAO) == repository.AUTHOR_ALREADY_ON
print("1) 후보로 채운 꺼진 작가는 구독하면 켜짐 OK")

# ── 2. 사용자가 직접 끈 작가는 구독해도 켜지지 않는다(네이버/카카오 모두) ──
repository.set_watched_author_enabled("후보작가", False, "후보작가", KAKAO)
assert repository.upsert_watched_author("후보작가", "후보작가", True, KAKAO) == repository.AUTHOR_USER_DISABLED and state("후보작가", KAKAO) is False
repository.upsert_watched_author("7", "네이버작가", True)
repository.set_watched_author_enabled("7", False, "네이버작가")
assert repository.upsert_watched_author("7", "네이버작가", True) == repository.AUTHOR_USER_DISABLED and state("7") is False
print("2) 직접 끈 작가는 켜지지 않음 OK")

# ── 3. 직접 끈 작가를 사용자가 다시 켜면 보호가 풀려서, 다시 끄기 전까지 정상으로 취급 ──
repository.set_watched_author_enabled("7", True, "네이버작가")
assert state("7") is True and repository.upsert_watched_author("7", "네이버작가", True) == repository.AUTHOR_ALREADY_ON
# 직접 끈 뒤 후보 채우기(꺼진 채로 등록)는 그 행을 건드리지 않는다
repository.set_watched_author_enabled("7", False, "네이버작가")
assert repository.upsert_watched_author("7", "네이버작가", False) == repository.AUTHOR_USER_DISABLED
print("3) 다시 켜면 보호 해제 / 후보 채우기가 보호를 못 풂 OK")

# ── 4. "작가/태그 관리"에서 직접 추가하는 것도 사용자의 선택이라 켜진다(직접 끈 작가여도) ──
async def api_scenarios():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        repository.set_watched_author_enabled("8", False, "직접끈")          # 직접 끈 작가
        r = await c.post("/api/watched-authors", json={"author_id": "8", "author_name": "직접끈"})
        assert r.status_code == 200 and r.json()["enabled"] is True and state("8") is True
        # 칩을 눌러 빼는 API로 끄면 보호가 걸린다
        assert (await c.post("/api/watched-authors/8/disable")).json()["enabled"] is False
        assert repository.upsert_watched_author("8", "직접끈", True) == repository.AUTHOR_USER_DISABLED
        # 칩 버튼으로 다시 켜기
        assert (await c.post("/api/watched-authors/8/enable")).json()["enabled"] is True
asyncio.run(api_scenarios())
print("4) 직접 추가/칩 조작 API OK")

# ── 5. 백업/복원 후에도 "직접 껐음"이 이어진다 ──
repository.set_watched_author_enabled("후보작가", False, "후보작가", KAKAO)    # 직접 끈 것
repository.upsert_watched_author("자동후보", "자동후보", False, KAKAO)        # 앱이 채운 후보
data = repository.export_all()
rows = {r["author_id"]: r for r in data["watched_authors"] if r["platform"] == KAKAO}
assert rows["후보작가"]["user_disabled"] == 1 and rows["자동후보"]["user_disabled"] == 0, rows
repository.restore_all(data)
assert repository.upsert_watched_author("후보작가", "후보작가", True, KAKAO) == repository.AUTHOR_USER_DISABLED
assert repository.upsert_watched_author("자동후보", "자동후보", True, KAKAO) == AUTO
# 이 항목이 없는 예전 백업도 복원된다(자동 후보로 취급)
for r in data["watched_authors"]: r.pop("user_disabled", None)
repository.restore_all(data)
assert repository.upsert_watched_author("후보작가", "후보작가", True, KAKAO) == AUTO
print("5) 백업/복원 OK (예전 백업은 자동 후보로 취급)")

print("ALL OK")
