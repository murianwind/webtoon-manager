"""빠른 상태 스캔(app/quick_scan.py) — 완결/휴재/요일만 가볍게 확인한다(작가/태그 등록 같은 무거운 일은 안 한다).

대상: 완결이 아닌 추적 작품 중 "요일별 목록(네이버)/요일 목록(카카오)에 없어서 이 앱이 직접 확인해야 하는 것"(장기 휴재 등) — 목록에 있는 작품은 목록이 이미 최신이다. 매일+ 숨김, 이미 완결인 작품 제외.
순서: **요일을 모르는 작품("기타")을 먼저**, 그다음 오늘 요일부터 요일별로 차례로. 최근에 확인한 작품은 건너뛴다(눌러도 매번 전부 다시 하지 않게; 실패는 15분 뒤 다시). 한 작품이 실패해도 계속한다.
네이버는 작품 정보 API 한 번(요일/완결/휴재), 카카오는 상태 확인 + (요일을 모르는 연재/휴재만) 개요. 카카오는 몰리지 않게 40개마다 쉰다.
"""
import asyncio
from datetime import datetime, timezone

from app import daily_plus, db, kakao_catalog, kakao_page_download as kp, kakao_status, naver_api, quick_scan, repository, weekdays
from app.config import get_settings
from app.models import TitlePage
db.get_connection()

# ── 시계/대기 ──
sleeps = []; _real = asyncio.sleep
async def fake_sleep(seconds, *a, **k): sleeps.append(seconds); await _real(0)
quick_scan.asyncio.sleep = fake_sleep

# Scenario 1: 요일 계산(한국시간)과 처리 순서 — 기타가 먼저, 그다음 오늘부터 돌아가며
assert quick_scan.today_key(datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)) == "tue"                                   # 한국시간 화요일 낮
assert quick_scan.today_key(datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)) == "wed", "UTC 16시 = 한국시간 다음 날 새벽 1시"
assert quick_scan.group_order("sat") == ["etc", "sat", "sun", "mon", "tue", "wed", "thu", "fri"] and quick_scan.group_order("mon")[:3] == ["etc", "mon", "tue"]
print("1) 요일/순서 OK")

# ── 데이터 ──
NOW = 1_800_000_000.0
def naver(tid, title, status, *, finished=False):
    repository.upsert_new(tid, title); repository.set_status(tid, status)
    if finished: repository.mark_finished(tid)
EX, UN, AC = repository.STATUS_EXCLUDED, repository.STATUS_UNSUBSCRIBED, repository.STATUS_ACTIVE
naver("n1", "가-휴재", EX); naver("n2", "나-완결됨", EX); naver("n3", "다-실패", EX); naver("n4", "라-예외", EX)
naver("n5", "마-월요일", UN); naver("n6", "바-목록에있음", AC); naver("n7", "사-이미완결", EX, finished=True); naver("n8", "아-매일플러스", EX); naver("n9", "자-방금확인", EX)
weekdays.save_lookup("n5", ["mon"], now=NOW - 3 * 86400, http=200, finished=False)                                          # 예전에 알아낸 요일(월)
weekdays.save_naver_map({"n6": ["tue"]})                                                                                    # 요일별 목록(live)에 있는 작품
weekdays.save_lookup("n9", [], now=NOW - 600, http=200, finished=False)                                                     # 10분 전에 확인(요일 없음)
daily_plus.cached_ids = lambda: {"n8"}
def kakao(tid, title, status, *, finished=False):
    repository.upsert_new_kakao_webtoon(tid, title, status=repository.STATUS_ACTIVE); repository.set_kakao_webtoon_status(tid, status)
    if finished: repository.set_kakao_finished(tid, True)
kakao(1, "카-기타", EX); kakao(2, "타-목록에있음", EX); kakao(3, "파-완결", EX, finished=True); kakao(4, "하-수요일", EX)
kakao_status.save(4, on_issue="Y", http=200, now=NOW - 3 * 86400, weekdays=["wed"])
kakao_catalog.snapshot = lambda: ([{"title_id": 2, "weekdays": ["fri"]}], None)

# Scenario 2: 대상 고르기 — 요일 목록/목록에 있는 작품, 완결, 매일+ 제외. 최근 확인한 작품은 건너뛴다(개수 보고). 그룹: 요일을 모르면 etc, 알면 그 요일(여러 요일이면 오늘 이후 가장 가까운 요일)
targets, skipped = quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)
ident = lambda t: (t.platform, str(t.title_id), t.group)
assert [ident(t) for t in targets] == [("naver", "n1", "etc"), ("naver", "n2", "etc"), ("naver", "n3", "etc"), ("naver", "n4", "etc"), ("kakao", "1", "etc"), ("kakao", "4", "wed"), ("naver", "n5", "mon")], [ident(t) for t in targets]
assert skipped == 1, "방금 확인한 n9"
weekdays.save_lookup("multi", ["thu", "mon"], now=NOW - 3 * 86400, http=200, finished=False); naver("multi", "차-여러요일", EX)
assert [t.group for t in quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)[0] if t.title_id == "multi"] == ["thu"], "오늘(화) 이후 가장 가까운 요일"
repository.set_status("multi", AC); weekdays.save_naver_map({"n6": ["tue"], "multi": ["thu"]}); assert "multi" not in [t.title_id for t in quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)[0]], "live 목록에 있으면 제외"
weekdays.save_naver_map({"n6": ["tue"]})
kakao(50, "카-판매중지", EX); kakao_status.save(50, on_issue="Y", http=200, state="ST62", now=NOW - 3 * 86400)
kakao(51, "카-서비스종료", EX); kakao_status.save(51, on_issue=None, http=404, now=NOW - 3 * 86400)
assert not ({50, 51} & {t.title_id for t in quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)[0]}), "판매 중지/서비스 종료로 확인된 카카오 작품은 다시 스캔하지 않는다(DB 완결 표시가 없어도)"
repository.set_kakao_finished(50, True); repository.set_kakao_finished(51, True)
print("2) 대상 고르기 OK")

# Scenario 3: 최근 확인 판단 — max_age 안이면 건너뛰고, 넘으면 포함. 실패한 확인은 15분이 지나면 다시
t2, s2 = quick_scan.collect_targets(today="tue", max_age_hours=100, now=NOW); assert "n5" not in [t.title_id for t in t2] and s2 >= 3, "3일 전 확인은 100시간 안이라 건너뜀"
weekdays.save_lookup("n3", None, now=NOW - 600, http=503)
assert "n3" not in [t.title_id for t in quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)[0]], "방금 실패한 것은 15분 안에는 다시 안 한다"
assert "n3" in [t.title_id for t in quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW + 1000)[0]], "15분이 지나면 다시"
weekdays._LOOKUP.edit(lambda m: m.pop("n3", None)); weekdays._LOOKUP.edit(lambda m: m.pop("multi", None)); repository.mark_finished("multi")                  # 이후 시나리오에서 빼려고 완결로
print("3) 최근 확인 OK")

# Scenario 4: 범위(scope) — 지정한 그룹만(새로고침이 "오늘/선택한 요일/기타"만 먼저 하게)
assert [str(t.title_id) for t in quick_scan.collect_targets(today="tue", scope={"etc"}, max_age_hours=1, now=NOW)[0]] == ["n1", "n2", "n3", "n4", "1"]
assert [str(t.title_id) for t in quick_scan.collect_targets(today="tue", scope={"wed", "mon"}, max_age_hours=1, now=NOW)[0]] == ["4", "n5"]
assert quick_scan.collect_targets(today="tue", scope=set(), max_age_hours=1, now=NOW)[0] == []
print("4) 범위 OK")

# ── 가짜 서버 ──
PAGES = {"n1": TitlePage(200, ["thu"], False, True), "n2": TitlePage(200, [], True, False), "n3": TitlePage(503), "n5": TitlePage(200, ["mon"], False, False)}
fetched = []
async def fake_weekdays(session, title_id, timeout):
    fetched.append(("naver", title_id))
    if title_id == "n4": raise RuntimeError("이 작품만 이상")
    return PAGES[title_id]
naver_api.fetch_title_weekdays = fake_weekdays
class FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
kp.new_session = lambda: FakeSession()
STATUS = {1: (200, {"on_issue": "Y", "state": "ST61"}), 4: (200, {"on_issue": "N", "state": "ST61"})}
async def fake_status(self, series_id): fetched.append(("kakao", series_id)); return STATUS[series_id]
async def fake_overview(self, series_id): return {"pub_period": "화"}
kp.KakaoPageClient.fetch_series_status = fake_status; kp.KakaoPageClient.fetch_overview = fake_overview
messages = []

# Scenario 5: 스캔 — 순서대로 확인하고(기타 먼저), 결과를 저장하며(요일, 완결, 휴재), 통계를 돌려준다. 예외/실패한 작품은 건너뛰고 계속한다. 진행 상황을 알린다
quick_scan.PROGRESS_EVERY = 3
targets, _ = quick_scan.collect_targets(today="tue", max_age_hours=1, now=NOW)
stats = asyncio.run(quick_scan.scan(targets, get_settings(), progress=messages.append))
assert fetched == [("naver", "n1"), ("naver", "n2"), ("naver", "n3"), ("naver", "n4"), ("kakao", 1), ("kakao", 4), ("naver", "n5")], fetched
assert (stats.scanned, stats.failed, stats.resolved_days, stats.newly_finished) == (7, 2, 2, 2), stats        # 요일을 새로 알아낸 것: n1(목), 카카오 1(화) — 이미 요일을 알던 n5/카카오 4는 세지 않는다
assert weekdays.get_naver_map()["n1"] == ["thu"] and repository.get("n1").is_paused is True, "요일과 휴재 저장"
assert repository.get("n2").is_finished is True and not repository.get("n1").is_finished, "완결 저장"
assert kakao_status.weekdays_of(1) == ["tue"] and kakao_status.is_hidden(kakao_status.get_lookup()["4"]), "카카오: 개요 요일, 완결"
assert weekdays._get_lookup()["n3"]["ok"] is False and weekdays._get_lookup()["n3"]["http"] == 503 and weekdays._get_lookup()["n4"]["ok"] is False, "실패는 기록돼 15분간 다시 안 한다"
assert messages and "3/7" in messages[0] and "7/7" in messages[-1], messages
print("5) 스캔 OK")

# Scenario 6: 카카오는 40개마다 쉰다(몰리지 않게) — 작품 사이 짧은 쉼과 묶음 사이 긴 쉼. 네이버는 작품 사이 짧은 쉼만
for i in range(100, 145): kakao(i, f"많음{i}", EX)
STATUS.update({i: (200, {"on_issue": "Y", "state": "ST61"}) for i in range(100, 145)})
sleeps.clear(); fetched.clear()
targets, _ = quick_scan.collect_targets(today="tue", scope={"etc"}, max_age_hours=0, now=NOW + 10)
kakao_targets = [t for t in targets if t.platform == "kakao"]; assert len(kakao_targets) == 45, len(kakao_targets)                                                                  # 방금 요일을 알아낸 카카오 1은 기타가 아니다
asyncio.run(quick_scan.scan(kakao_targets, get_settings()))
assert sleeps.count(quick_scan.KAKAO_BATCH_REST_SECONDS) == 1 and sleeps.count(quick_scan.KAKAO_PAUSE_SECONDS) == 45, (sleeps.count(quick_scan.KAKAO_BATCH_REST_SECONDS), sleeps.count(quick_scan.KAKAO_PAUSE_SECONDS))
sleeps.clear(); asyncio.run(quick_scan.scan(kakao_targets[:40], get_settings()))
assert quick_scan.KAKAO_BATCH_REST_SECONDS not in sleeps, "정확히 한 묶음이면(마지막 작품 뒤) 쉬지 않는다"
print("6) 카카오 쉼 OK")

# Scenario 7: 빈 대상은 아무것도 안 하고 통계는 0
stats = asyncio.run(quick_scan.scan([], get_settings())); assert (stats.scanned, stats.failed, stats.resolved_days, stats.newly_finished) == (0, 0, 0, 0)
print("ALL OK")
