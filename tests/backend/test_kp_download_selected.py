"""수동 다운로드(download_selected) — 분석 표에서 고른 회차들을 번호순으로 받는다. 이미 받은 회차도 다시 받아 교체하고, 잠긴 회차는 기다무/대여권으로 열 수 있을 때만 열고
(못 열면 이유와 함께 건너뜀), 하나가 실패해도 나머지는 계속한다. 이 함수의 분기(잠긴 회차를 여는 규칙, 결과 구분, 진행 문구)를 그대로 고정한다.
실제 응답 형태를 따른 가짜 서버(fake_kakao_rental_site)를 쓴다.
"""
import asyncio
import tempfile
from pathlib import Path

from app import db
db.get_connection()
from app import kakao_cover, kakao_page_download as kp, repository
from fake_kakao_rental_site import JPG, Site, item

_orig = asyncio.sleep
async def _ns(x): await _orig(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG

def run(site, numbers, *, rental=False, root=None, title=None, client_hook=None):
    root = root or Path(tempfile.mkdtemp()); msgs = []
    client = kp.KakaoPageClient(site, {}, 10)
    if client_hook: client_hook(client)
    result = asyncio.run(kp.download_selected(client, series_id=1, title=title, numbers=numbers, download_root=str(root), on_progress=msgs.append, use_rental_tickets=rental))
    return result, msgs, root
zips = lambda root: sorted(p.name for p in Path(root).rglob("*.zip"))
free = lambda n: item(n, free=True)
locked = lambda n: item(n)                       # 잠김(기다무로 열 수 있는 일반 잠긴 회차)
blocked = lambda n: item(n, blocked=True)        # 기다무로 못 여는 회차

# Scenario 1: 목록을 못 받으면(작품 정보 없음) 고른 번호 전부(중복 제거, 번호순)를 "찾을 수 없음"으로
def no_listing(client):
    async def none(series_id): return None
    client.list_episodes = none
result, msgs, _ = run(Site([free(1)]), [3, 1, 3], client_hook=no_listing)
assert result.not_found == [1, 3] and result.downloaded == [] and result.title == "" and msgs == [], (result, msgs)

# Scenario 2: 무료 회차 — 번호순으로 받고(중복 선택은 한 번), 제목은 직접 준 게 없으면 사이트의 작품 제목, 받은 (번호, 부제목) 목록과 메타데이터(info.xml)를 남긴다. 목록에 없는 번호는 not_found
result, msgs, root = run(Site([free(1), free(2), free(3)]), [2, 1, 2, 99])
assert result.downloaded == [1, 2] and result.not_found == [99] and result.failed == [] and result.replaced == [] and result.title == "작품", result
assert [n for n, _ in result.downloaded_items] == [1, 2] and len(zips(root)) == 2, zips(root)
assert msgs[0].startswith("✅ 1번 회차 완료 (") and msgs[1].startswith("✅ 2번 회차 완료 (") and any((root / "작품").glob("info.xml")), msgs
result2, _, root2 = run(Site([free(1)]), [1], title="내가 정한 제목"); assert result2.title == "내가 정한 제목" and (root2 / "내가 정한 제목").exists()
print("1~2) 목록 없음/무료 회차 OK")

# Scenario 3: 이미 받은 회차를 다시 고르면 다시 받아 교체한다(replaced에 담기고 문구가 다르다). 받는 데 실패하면 실패로 세고 "기존 파일은 그대로 둡니다"를 알리며, 처음 받는 것의 실패에는 그 문구가 없다
site = Site([free(1), free(2)]); _, _, root = run(site, [1, 2])
result, msgs, _ = run(site, [1], root=root); assert result.downloaded == [1] and result.replaced == [1] and "✅ 1번 회차 다시 받아 교체 (" in msgs[0], (result, msgs)
site.no_image = {5002}
result, msgs, _ = run(site, [2], root=root); assert result.failed == [2] and result.downloaded == [] and msgs[0] == "❌ 2번 회차 받기 실패 (기존 파일은 그대로 둡니다)", msgs
result, msgs, _ = run(Site([free(1), free(2)], no_image=[5002]), [2]); assert result.failed == [2] and msgs[0] == "❌ 2번 회차 받기 실패", msgs
result, msgs, _ = run(Site([free(1), free(2)], no_image=[5001]), [1, 2]); assert result.failed == [1] and result.downloaded == [2], "하나가 실패해도 나머지는 계속한다"
# 받은 회차 기록에는 있는데 폴더에는 없는(아카이빙으로 옮겨진) 회차를 다시 받는 것은 폴더의 파일을 바꾸는 게 아니다 — 교체로 세지 않고 문구도 "완료"
repository.upsert_new_kakao_webtoon(1, "작품"); repository.set_kakao_downloaded_numbers(1, [1, 2])
result, msgs, _ = run(Site([free(1), free(2)]), [1]); assert result.downloaded == [1] and result.replaced == [] and msgs[0].startswith("✅ 1번 회차 완료 ("), (result, msgs)
repository.set_kakao_downloaded_numbers(1, [])
print("3) 교체/실패 OK")

# Scenario 4: 잠긴 회차를 기다무로 연다 — 충전돼 있고 열 수 있으면 열고(ticket_used) 받는다. 기다무는 한 장이라 고른 잠긴 회차 중 번호가 가장 앞선 하나만 열고, 나머지는 이유와 함께 건너뛴다. 잠겨 있어도 뒤의 무료 회차는 계속 받는다
site = Site([free(1), locked(3), locked(4), free(5)], waitfree=True)
result, msgs, root = run(site, [5, 4, 3, 1])
assert result.ticket_used == [3] and result.downloaded == [1, 3, 5] and result.skipped_locked == [4] and result.rental_used == [], result
assert "3번 회차를 기다무로 열었습니다" in msgs and "4번 회차: 기다무는 한 장이라 하나만 열 수 있습니다 — 건너뜀" in msgs, msgs
assert msgs.index("3번 회차를 기다무로 열었습니다") < [i for i, m in enumerate(msgs) if m.startswith("✅ 3번")][0], "연 뒤에 받는다"
print("4) 기다무 열기 OK")

# Scenario 5: 열 수 없는 이유별 문구 — 충전 전(기본 문구), 작품에 기다무가 없음, 기다무로 열 수 없는 회차
result, msgs, _ = run(Site([locked(3)], waitfree=False), [3]); assert result.skipped_locked == [3] and "3번 회차: 잠김(대여권 필요) — 건너뜀" in msgs and result.downloaded == [], msgs
result, msgs, _ = run(Site([locked(3)], waitfree=True, series_waitfree=False), [3]); assert "3번 회차: 이 작품은 기다무가 없습니다 — 건너뜀" in msgs and result.skipped_locked == [3], msgs
site = Site([blocked(3)], waitfree=True); result, msgs, _ = run(site, [3]); assert "3번 회차: 기다무로 열 수 없는 회차입니다 — 건너뜀" in msgs and result.skipped_locked == [3], msgs
assert not any(g.endswith("ticket/my") for g in site.gets) and site.posts == [], "기다무로 열 수 없다고 이미 알면 이용권 조회/사용 요청을 하지 않는다"
site = Site([locked(3)], waitfree=True, series_waitfree=False); run(site, [3]); assert not any(g.endswith("ticket/my") for g in site.gets), "기다무가 없는 작품도 이용권 조회를 하지 않는다"
site = Site([locked(3), locked(4)], waitfree=False); result, _, _ = run(site, [3, 4]); assert result.skipped_locked == [3, 4] and sum(g.endswith("ticket/my") for g in site.gets) == 1, "이용권 정보는 한 번만 조회한다"
print("5) 건너뜀 이유 OK")

# Scenario 6: 대여권 — 켜져 있을 때만, 기다무로 못 여는 회차를 대여권으로 연다(보유 수를 넘으면 나머지는 "대여권이 부족합니다"로 건너뜀). 남은 장수를 알린다. 꺼져 있으면 쓰지 않는다
site = Site([blocked(3), blocked(4), blocked(5)], rental=2)
result, msgs, _ = run(site, [3, 4, 5], rental=True)
assert result.rental_used == [3, 4] and result.downloaded == [3, 4] and result.skipped_locked == [5] and result.rental_remaining == 0 and result.ticket_used == [], result
assert "3번 회차를 대여권으로 열었습니다 (남은 대여권 1장)" in msgs and "4번 회차를 대여권으로 열었습니다 (남은 대여권 0장)" in msgs and "5번 회차: 대여권이 부족합니다 — 건너뜀" in msgs, msgs
site = Site([blocked(3)], rental=2); result, msgs, _ = run(site, [3], rental=False); assert result.rental_used == [] and result.skipped_locked == [3] and [p for p in site.posts if p[0].endswith("ticket/use")] == [], msgs
site = Site([locked(3)], waitfree=False, rental=2); result, msgs, _ = run(site, [3], rental=True)
assert result.rental_used == [] and result.skipped_locked == [3] and not [p for p in site.posts if p[0].endswith("ticket/use")] and site.rental == 2, "기다무로 열 수 있는 회차(충전 전)에는 대여권을 쓰지 않는다"
assert not any("ready_to_use" in g for g in site.gets), "자격이 없으면 대여권 확인 요청도 하지 않는다(대여권 쪽에서도 막지만 요청 자체를 아낀다)"
site = Site([locked(3)], waitfree=True, series_waitfree=False, rental=2); result, _, _ = run(site, [3], rental=True); assert result.rental_used == [3] and site.rental == 1, "기다무가 없는 작품의 잠긴 회차는 대여권 대상"
print("6) 대여권 OK")

# Scenario 7: 열려는데 사용이 거절되면(서버 거절) 건너뛴다 — 받지 않고 다음 회차는 계속
site = Site([blocked(3), free(4)], rental=2, fail_use=[5003]); result, msgs, _ = run(site, [3, 4], rental=True)
assert result.skipped_locked == [3] and result.downloaded == [4] and result.rental_used == [], (result, msgs)
print("ALL OK")
