"""카카오페이지 "받은 회차 기록" 직접 고치기 — 수동 다운로드에서 구독 중인 작품의 회차를 "받은 것으로" 또는 "받지 않은 것으로" 기록한다.

- 받은 것으로: 기록에 더하고 "받을 대상 지정"에서 뺀다(폴더 파일은 건드리지 않는다).
- 받지 않은 것으로: 기록에서 빼고 "받을 대상"으로 지정한다 — 시작 지점(가장 앞 기록)보다 앞 회차여도 자동으로 받게 된다. 폴더에 파일이 있는 회차는 파일이 있는 한 받은 것이라 바꿀 수 없다.
- 구독 중(active)인 작품만 가능하다.
"""
import asyncio
import tempfile
from pathlib import Path

import httpx

from app import db, kakao_records, repository
from app import kakao_page_download as kp
from fake_kakao_rental_site import JPG, Site, item
db.get_connection()
import app.main as m

kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
SID = 69500409


def ep(n, free=False, acc=False):
    return kp.Episode(product_id=n, title=f"{n - 1}화", number=n, subtitle=f"{n - 1}화", is_free=free, accessible=free or acc, page_count=1, hidden=False, waitfree_blocked=n >= 24)


EPISODES = [ep(n, free=True) for n in range(1, 7)] + [ep(n) for n in range(7, 27)]
MARKER = [kp.ExistingFile(number=23, name="0023_22화.zip", subtitle="22화")]

# Scenario 1: 받을 대상 지정(pending) 저장소 — 기본은 빈 집합, 추적하지 않는 작품도 빈 집합, 넣고 빼기
repository.upsert_new_kakao_webtoon(SID, "도굴왕: 엔드라인", status=repository.STATUS_ACTIVE)
assert repository.get_kakao_pending_numbers(SID) == set() and repository.get_kakao_pending_numbers(999) == set()
repository.set_kakao_pending_numbers(SID, {5, 9, 12}); assert repository.get_kakao_pending_numbers(SID) == {5, 9, 12}
repository.remove_kakao_pending_numbers(SID, [9, 77]); assert repository.get_kakao_pending_numbers(SID) == {5, 12}
repository.set_kakao_pending_numbers(SID, set())

# Scenario 2: 계획 — 기록 {23}, 폴더 23이면 23 이전은 시작 지점 앞이라 받지 않지만, 받을 대상으로 지정한 회차는 받는다(잠겼으면 잠긴 회차로)
plan = kp.plan_by_folder_rules(EPISODES, MARKER, "", {23})
assert plan.to_download == [] and [e.number for e in plan.locked] == [24, 25, 26] and sum(1 for r in plan.rows if r.before_start) == 22
plan = kp.plan_by_folder_rules(EPISODES, MARKER, "", {23}, pending={3, 10})
assert [e.number for e in plan.to_download] == [3] and [e.number for e in plan.locked] == [10, 24, 25, 26], ([e.number for e in plan.to_download], [e.number for e in plan.locked])
assert [r.before_start for r in plan.rows if r.episode.number in (3, 10)] == [False, False] and plan.rows[0].before_start is True     # 1번은 그대로 이전 회차
# 이미 받은(폴더에 있는) 회차는 받을 대상으로 지정돼도 받은 것이 이긴다
plan = kp.plan_by_folder_rules(EPISODES, MARKER, "", {23}, pending={23})
assert all(e.number != 23 for e in plan.to_download + plan.locked)
print("1~2) 저장소/계획 OK")


def folder_state(numbers):
    return [kp.ExistingFile(number=n, name=f"{n:04d}_{n - 1}화.zip", subtitle=f"{n - 1}화") for n in numbers]


# Scenario 3: 받은 것으로 기록 — 기록이 없으면 폴더(+아카이빙 이력)에서 만든 기준에 더하고, 받을 대상 지정에서는 뺀다
repository.upsert_new_kakao_webtoon(SID + 1, "다른 작품", status=repository.STATUS_ACTIVE)
repository.list_archive_file_names = lambda tid: [f"{n:04d}_x.zip" for n in (1, 2)] if tid == f"kakao_{SID + 1}" else []
repository.set_kakao_pending_numbers(SID + 1, {8, 9})
result = kakao_records.mark(SID + 1, {7, 8}, True, folder_state([23]))
assert repository.get_kakao_downloaded_numbers(SID + 1) == {1, 2, 7, 8, 23}, repository.get_kakao_downloaded_numbers(SID + 1)    # 폴더 23 + 아카이빙 이력 1,2 + 표시한 7,8
assert repository.get_kakao_pending_numbers(SID + 1) == {9} and result["marked"] == [7, 8] and result["ignored_in_folder"] == [] and result["recorded_count"] == 5

# Scenario 4: 받지 않은 것으로 기록 — 기록에서 빼고 받을 대상으로 지정, 폴더에 파일이 있는 회차는 바꿀 수 없다고 알려 준다
repository.set_kakao_downloaded_numbers(SID, set(range(1, 24)))
result = kakao_records.mark(SID, {7, 8, 23}, False, folder_state([23]))
assert repository.get_kakao_downloaded_numbers(SID) == set(range(1, 24)) - {7, 8} and repository.get_kakao_pending_numbers(SID) == {7, 8}
assert result["marked"] == [7, 8] and result["ignored_in_folder"] == [23], result
plan = kp.plan_by_folder_rules(EPISODES, folder_state([23]), "", repository.get_kakao_downloaded_numbers(SID), pending=repository.get_kakao_pending_numbers(SID))
assert [e.number for e in plan.locked][:2] == [7, 8] and plan.to_download == []      # 7, 8이 시작 지점(1번) 뒤의 빠진 회차로 다시 받을 대상
# Scenario 5: 도굴왕 — 기록 {1~6, 23}, 폴더 23 상태에서 7~22를 받은 것으로 기록하면 앞 회차가 모두 받은 것으로 보이고 받을 회차는 없다
repository.set_kakao_downloaded_numbers(SID, {1, 2, 3, 4, 5, 6, 23}); repository.set_kakao_pending_numbers(SID, set())
kakao_records.mark(SID, set(range(7, 23)), True, folder_state([23]))
plan = kp.plan_by_folder_rules(EPISODES, folder_state([23]), "", repository.get_kakao_downloaded_numbers(SID), pending=repository.get_kakao_pending_numbers(SID))
assert plan.to_download == [] and [e.number for e in plan.locked] == [24, 25, 26] and all(r.downloaded for r in plan.rows if r.episode.number <= 23)
print("3~5) 기록 고치기 OK")


async def api():
    root = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(root))
    folder = Path(kp.series_folder(str(root), "도굴왕: 엔드라인")); folder.mkdir(parents=True); (folder / "0023_22화.zip").write_bytes(b"x")
    repository.set_kakao_downloaded_numbers(SID, {1, 2, 3}); repository.set_kakao_pending_numbers(SID, set())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        post = lambda body: c.post("/api/kakao-manual/record", json=body)
        # Scenario 6: 구독 중인 작품은 기록을 바꿀 수 있고(폴더의 파일도 함께 읽는다), 결과를 돌려준다
        r = await post({"series_id": SID, "numbers": [7, 8, 9], "downloaded": True})
        assert r.status_code == 200 and r.json()["marked"] == [7, 8, 9], r.text
        assert repository.get_kakao_downloaded_numbers(SID) == {1, 2, 3, 7, 8, 9, 23}      # 폴더에 있는 23번도 기록에 보태진다(폴더 기준으로 일치시키는 동작)
        r = await post({"series_id": SID, "numbers": [8, 23], "downloaded": False})
        assert r.status_code == 200 and r.json()["marked"] == [8] and r.json()["ignored_in_folder"] == [23]
        assert repository.get_kakao_pending_numbers(SID) == {8}
        # Scenario 7: 구독하지 않은(구독해제/제외됨/추적 안 함) 작품, 빈 선택, 잘못된 번호는 400
        repository.set_kakao_webtoon_status(SID, repository.STATUS_UNSUBSCRIBED)
        r = await post({"series_id": SID, "numbers": [7], "downloaded": True})
        assert r.status_code == 400 and "구독" in r.json()["detail"], r.text
        repository.set_kakao_webtoon_status(SID, repository.STATUS_ACTIVE)
        assert (await post({"series_id": 424242, "numbers": [7], "downloaded": True})).status_code == 400
        assert (await post({"series_id": SID, "numbers": [], "downloaded": True})).status_code == 400
        assert (await post({"series_id": SID, "numbers": [0, -3], "downloaded": True})).status_code == 400
    print("6~7) 기록 API OK")


async def clearing_pending_on_download():
    # Scenario 8: 받을 대상으로 지정한 회차를 실제로 받으면 지정이 풀리고 기록에 더해진다
    root = Path(tempfile.mkdtemp())
    repository.set_kakao_downloaded_numbers(SID, {1}); repository.set_kakao_pending_numbers(SID, {1, 5})
    client = kp.KakaoPageClient(Site([item(1, free=True)]), {}, 10)
    path = await kp.download_episode(client, SID, kp.Episode(product_id=5001, title="t", number=1, subtitle="1화", is_free=True, accessible=True, page_count=1, hidden=False), root / "작품")
    assert path is not None and repository.get_kakao_pending_numbers(SID) == {5} and 1 in repository.get_kakao_downloaded_numbers(SID)
    print("8) 받으면 지정이 풀림 OK")

asyncio.run(api())
asyncio.run(clearing_pending_on_download())
print("ALL OK")
