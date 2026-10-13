"""네이버 "받은 회차 기록" 직접 고치기 — 네이버는 회차마다가 아니라 "여기까지 받음"(last_downloaded_no) 한 지점만 기억한다.

- 받은 것으로 기록: 고른 회차 중 가장 큰 번호까지 받은 것으로(이미 그보다 뒤까지 기록돼 있으면 그대로).
- 받지 않은 것으로 기록: 고른 회차 중 가장 작은 번호부터 모두 받을 대상이 되도록 그 앞 번호로 되돌린다(고른 것만이 아니라 그 뒤 전부 — 한 지점만 기억해서).
- 폴더에 파일이 있는 회차는 다음 실행에서 폴더 기준으로 다시 받은 것으로 인식되어 되돌아오므로, 그렇게 되는 경우 알려 준다.
- 구독 중인 작품만, 잠긴 회차(유료/미공개)는 기록할 수 없다. 분석 화면에는 기록으로만 받은 것이 구분돼 보인다(recorded).
"""
import asyncio

import httpx

from app import db, manual_download, naver_api, repository
from app.models import EpisodeInfo, TitleInfo
db.get_connection()
import app.main as m

TID = "333"
INFO = TitleInfo(title_id=TID, title_name="네이버 작품", synopsis="", is_adult=False, webtoon_type="webtoon", is_finished=False, thumbnail_url="")
EPISODES = [EpisodeInfo(n, f"{n}화", False) for n in range(1, 23)] + [EpisodeInfo(23, "23화", True)]      # 23화는 유료/잠김
FOLDER_LAST = {"value": 0}


async def fake_info(session, title_id, timeout): return INFO
async def fake_all(session, title_id, cookies, timeout): return list(EPISODES)
naver_api.fetch_title_info, naver_api.fetch_all_episodes = fake_info, fake_all
manual_download.find_last_downloaded_episode_no = lambda directory, episodes: FOLDER_LAST["value"]

repository.upsert_new(TID, "네이버 작품")
last = lambda: repository.get(TID).last_downloaded_no


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        post = lambda body: c.post("/api/manual-download/record", json=body)
        analyze = lambda: c.get("/api/manual-download/analyze", params={"title_id": TID})

        # Scenario 1: 분석 화면 — 기록(last)이 폴더보다 뒤면 그 사이는 "기록만"(recorded)으로 구분되어 받은 것으로 보인다. 구독하지 않으면 폴더 기준만
        repository.update_last_downloaded_no(TID, 10); FOLDER_LAST["value"] = 5
        rows = {e["episode_no"]: e for e in (await analyze()).json()["episodes"]}
        assert rows[5]["owned"] and not rows[5]["recorded"] and rows[8]["owned"] and rows[8]["recorded"] and not rows[11]["owned"] and not rows[11]["recorded"]
        repository.set_status(TID, repository.STATUS_UNSUBSCRIBED)
        rows = {e["episode_no"]: e for e in (await analyze()).json()["episodes"]}
        assert rows[5]["owned"] and not rows[8]["owned"] and not rows[8]["recorded"]
        repository.set_status(TID, repository.STATUS_ACTIVE)

        # Scenario 2: 받은 것으로 — 고른 가장 큰 번호까지(이미 더 뒤까지 기록돼 있으면 그대로), 잠긴 회차는 무시하고 알려 준다
        repository.update_last_downloaded_no(TID, 0); FOLDER_LAST["value"] = 0
        r = (await post({"title_id": TID, "episode_nos": list(range(1, 23)), "downloaded": True})).json()
        assert last() == 22 and r["previous"] == 0 and r["last_downloaded_no"] == 22 and r["marked"] == list(range(1, 23)) and r["ignored_locked"] == [] and r["warning"] is None, r
        r = (await post({"title_id": TID, "episode_nos": [5, 23], "downloaded": True})).json()
        assert last() == 22 and r["ignored_locked"] == [23] and r["marked"] == [5] and r["last_downloaded_no"] == 22        # 22보다 앞이라 그대로

        # Scenario 3: 받지 않은 것으로 — 고른 가장 작은 번호부터 받을 대상(8, 9를 골라도 8 이후 전부), 폴더가 그보다 뒤까지 있으면 경고
        FOLDER_LAST["value"] = 0
        r = (await post({"title_id": TID, "episode_nos": [9, 8], "downloaded": False})).json()
        assert last() == 7 and r["previous"] == 22 and r["last_downloaded_no"] == 7 and r["marked"] == [8, 9] and r["warning"] is None, r
        repository.update_last_downloaded_no(TID, 22); FOLDER_LAST["value"] = 12
        r = (await post({"title_id": TID, "episode_nos": [8], "downloaded": False})).json()
        assert last() == 7 and r["folder_last_no"] == 12 and r["warning"] and "12화" in r["warning"] and "다시" in r["warning"], r
        # 이미 그보다 앞으로 기록돼 있으면 그대로(올리지 않는다)
        repository.update_last_downloaded_no(TID, 3); FOLDER_LAST["value"] = 0
        assert (await post({"title_id": TID, "episode_nos": [8], "downloaded": False})).json()["last_downloaded_no"] == 3 and last() == 3
        r = (await post({"title_id": TID, "episode_nos": [1], "downloaded": False})).json()
        assert last() == 0 and r["last_downloaded_no"] == 0                                                                  # 1화부터 받지 않은 것으로 → 처음부터

        # Scenario 4: 구독 중인 작품만, 선택이 없거나 잠긴 회차뿐이거나 목록에 없는 번호뿐이면 400
        repository.set_status(TID, repository.STATUS_UNSUBSCRIBED)
        r = await post({"title_id": TID, "episode_nos": [1], "downloaded": True}); assert r.status_code == 400 and "구독" in r.json()["detail"], r.text
        repository.set_status(TID, repository.STATUS_ACTIVE)
        assert (await post({"title_id": "9999", "episode_nos": [1], "downloaded": True})).status_code == 400          # 추적하지 않는 작품
        assert (await post({"title_id": TID, "episode_nos": [], "downloaded": True})).status_code == 400
        r = await post({"title_id": TID, "episode_nos": [23], "downloaded": True}); assert r.status_code == 400 and "잠긴" in r.json()["detail"], r.text
        assert (await post({"title_id": TID, "episode_nos": [500], "downloaded": True})).status_code == 400
        assert (await post({"title_id": "abc", "episode_nos": [1], "downloaded": True})).status_code == 400
        # Scenario 5: 작품 정보를 못 받으면 502(아무것도 바꾸지 않는다)
        async def none_info(session, title_id, timeout): return None
        naver_api.fetch_title_info = none_info
        before = last(); r = await post({"title_id": TID, "episode_nos": [1], "downloaded": True})
        assert r.status_code == 502 and last() == before, r.text
    print("ALL OK")

asyncio.run(main())
