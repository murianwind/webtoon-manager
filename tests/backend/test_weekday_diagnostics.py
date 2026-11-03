"""요일 진단(weekday_diagnostics) — "기타"에 왜 남았는지를 HAR 없이 원인별 개수로 보여 준다.
네이버: 완결 / 작품 페이지에 요일 없음 / 확인 실패(HTTP 상태별, 네트워크 오류) / 확인 대기. 카카오: 완결 / 서비스 종료 / 휴재 / 연재 중인데 요일 목록에 없음 / 확인 실패 / 확인 대기.
이미 요일을 아는 작품(목록 또는 조회 결과)은 세지 않는다. 구독해제 목록은 구독한 적 있는 작품만(화면과 같은 규칙).
"""
import asyncio

import httpx

from app import db, kakao_catalog, kakao_status, repository, weekdays, weekday_diagnostics
db.get_connection()
import app.main as m

# ── 네이버 작품 ──
def naver(tid, name, status):
    repository.upsert_new(tid, name); repository.set_status(tid, status)
EX, UN, AC = repository.STATUS_EXCLUDED, repository.STATUS_UNSUBSCRIBED, repository.STATUS_ACTIVE
naver("n1", "네이버완결", EX); repository.update_is_finished("n1", True)
naver("n2", "네이버페이지에요일없음", EX); weekdays.save_lookup("n2", [], http=200, finished=False)
naver("n3", "네이버403", EX); weekdays.save_lookup("n3", None, http=403)
naver("n4", "네이버네트워크오류", EX); weekdays.save_lookup("n4", None, http=None)
naver("n5", "네이버대기", EX)
naver("n6", "네이버요일앎", EX); weekdays.save_lookup("n6", ["mon"], http=200, finished=False)                                            # 조회로 요일을 알아낸 작품은 세지 않는다
naver("n7", "네이버구독해제대기", UN); repository.set_status("n7", AC); repository.set_status("n7", UN)                                  # 구독한 적 있음
naver("n8", "네이버구독중대기", AC)
naver("n9", "네이버구독한적없는구독해제", UN)                                                                                              # 구독한 적 없는 구독해제는 화면에 안 나온다 → 세지 않는다
with db.write_transaction() as cx: cx.execute("UPDATE webtoons SET ever_subscribed = 0 WHERE title_id = 'n9'")

# ── 카카오 작품 ──
def kakao(tid, name, status):
    repository.upsert_new_kakao_webtoon(tid, name, status=AC); repository.set_kakao_webtoon_status(tid, status)
for tid, name in ((1, "카카오완결"), (2, "카카오종료"), (3, "카카오휴재"), (4, "카카오연재인데목록에없음"), (5, "카카오대기"), (6, "카카오503"), (7, "카카오요일앎")): kakao(tid, name, EX)
kakao_status.save(1, on_issue="N", http=200); kakao_status.save(2, on_issue=None, http=404); kakao_status.save(3, on_issue="P", http=200)
kakao_status.save(4, on_issue="Y", http=200); kakao_status.save(6, on_issue=None, http=503)
CATALOG = [{"title_id": 7, "weekdays": ["wed"]}, {"title_id": 99, "weekdays": ["mon"]}]
kakao_catalog.snapshot = lambda: (CATALOG, None, False)

# Scenario 1: 제외됨만 — 원인별 개수, 확인 실패는 상태별로, 요일을 아는 작품/구독한 적 없는 구독해제는 제외
result = weekday_diagnostics.summarize(EX)
nv, kk = result["naver"], result["kakao"]
assert nv["total"] == 5 and nv["reasons"] == {"finished": 1, "no_weekday_on_page": 1, "check_failed": 2, "pending": 1}, nv
assert nv["failed_http"] == {"403": 1, "network": 1}, nv["failed_http"]
assert sorted(nv["samples"]["check_failed"]) == ["네이버403", "네이버네트워크오류"], nv["samples"]
assert kk["total"] == 6 and kk["reasons"] == {"finished": 1, "ended": 1, "hiatus": 1, "serving_not_listed": 1, "pending": 1, "check_failed": 1}, kk
assert kk["failed_http"] == {"503": 1} and kk["samples"]["ended"] == ["카카오종료"]
print("1) 제외됨 요약 OK")

# Scenario 2: 상태를 안 주면 모든 상태(구독중/구독해제/제외됨)를 합친다. 구독해제는 구독한 적 있는 작품만
allr = weekday_diagnostics.summarize(None)
assert allr["naver"]["total"] == 5 + 2 and allr["naver"]["reasons"]["pending"] == 3, allr["naver"]          # 대기: n5(제외됨) + n7(구독해제) + n8(구독중)
print("2) 전체 상태 OK")

# Scenario 3: 샘플은 원인마다 최대 5개
for i in range(10): naver(f"many{i}", f"많음{i}", EX)
r = weekday_diagnostics.summarize(EX); assert r["naver"]["reasons"]["pending"] == 11 and len(r["naver"]["samples"]["pending"]) == 5
print("3) 샘플 상한 OK")

# Scenario 4: 카카오 목록(요일별 목록 캐시)을 아직 못 받았으면 "연재인데 목록에 없음"으로 단정하지 않는다(목록 미확인)
CATALOG.clear()
r = weekday_diagnostics.summarize(EX); assert "serving_not_listed" not in r["kakao"]["reasons"] and r["kakao"]["reasons"].get("catalog_not_loaded") == 1 and r["kakao"]["reasons"]["pending"] == 2 and r["kakao"]["total"] == 7, r["kakao"]        # 목록이 비면 요일 아는 작품(7)도 모르는 쪽(대기)으로
CATALOG.extend([{"title_id": 7, "weekdays": ["wed"]}])
print("4) 목록 미확인 OK")

# Scenario 5: API — /api/diagnostics/weekday-gaps?status=..., 잘못된 상태는 400
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        ok = await c.get("/api/diagnostics/weekday-gaps", params={"status": "excluded"}); assert ok.status_code == 200
        body = ok.json(); assert body["status"] == "excluded" and body["naver"]["reasons"]["finished"] == 1 and body["kakao"]["reasons"]["ended"] == 1
        assert (await c.get("/api/diagnostics/weekday-gaps")).json()["status"] is None
        assert (await c.get("/api/diagnostics/weekday-gaps", params={"status": "엉뚱"})).status_code == 400
asyncio.run(api())
print("ALL OK")
