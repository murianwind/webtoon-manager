"""카카오 "받은 회차 기록" — 무료는 바로, 기다무는 가장 앞의 빈 회차부터 순서대로. 아카이빙으로 파일이 옮겨져도 빈 회차를 잊지 않는다.
계획 규칙(순수 함수) / 저장소 / 회차 저장 지점(자동·수동 공통) / 시작 시 소급 / 분석 화면 / 백업."""
import asyncio, json, tempfile
from pathlib import Path

import httpx

from app import db, kakao_page_download as kp, kakao_records, repository
from app.config import get_settings
from app.file_utils import remove_forbidden_str_kakao
db.get_connection()
import app.main as m

ST = get_settings()
KROOT = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(KROOT))

def ep(n, acc=True, hidden=False): return kp.Episode(product_id=n, title=f"작품 {n}화", number=n, subtitle=f"{n}화", is_free=acc, accessible=acc, page_count=1, hidden=hidden)
def eps(total, locked=()): return [ep(n, acc=n not in locked) for n in range(1, total + 1)]
def files(*numbers): return [kp.ExistingFile(number=n, subtitle=f"{n}화", name=f"{n:04d}_{n}화.zip") for n in numbers]
def nums(rows): return [e.number for e in rows]

# ═══ 1. 계획 규칙 (순수 함수) ═══
# 1-1. 말씀하신 상황: 317~363 기다무 잠김, 364~447 받음, 448 새로 무료 → 448은 바로 받고, 317부터 기다무로 순서대로
F = list(range(300, 317)) + list(range(364, 448))
plan = kp.plan_by_folder_rules(eps(448, locked=range(317, 364)), files(*F), "작품", recorded=set(F))
assert plan.mode == "compare" and nums(plan.to_download) == [448] and nums(plan.locked) == list(range(317, 364)), (plan.mode, nums(plan.to_download), nums(plan.locked)[:3])
assert all(r.before_start for r in plan.rows if r.episode.number < 300) and not any(r.archived for r in plan.rows)
print("1-1) 무료는 바로(448), 잠긴 구간(317~363)은 가장 앞부터 OK")
# 1-2. 아카이빙 뒤: 폴더에는 마지막 파일(447)만 남았지만 기록이 있으면 옮겨진 회차를 받은 것으로 본다(표식 한 개 규칙으로 앞 회차가 전부 잘려 나가지 않는다)
plan = kp.plan_by_folder_rules(eps(448), files(447), "작품", recorded=set(range(1, 448)))
assert plan.mode == "compare" and plan.marker is None and nums(plan.to_download) == [448]
assert sum(r.archived for r in plan.rows) == 446 and all(r.downloaded and not r.before_start for r in plan.rows if r.episode.number <= 447)
assert [r.archived for r in plan.rows if r.episode.number == 447] == [False]                                                    # 폴더에 있는 파일은 "보관"이 아니다
print("1-2) 아카이빙 뒤에도 앞 회차를 받은 것으로 봄 OK (보관 446개)")
# 1-3. 450을 실패한 채 아카이빙이 지나가도(폴더에는 451만 남음) 450은 빈 회차로 남아서 다시 받는다
plan = kp.plan_by_folder_rules(eps(452), files(451), "작품", recorded={447, 448, 449, 451})
assert nums(plan.to_download) == [450, 452], nums(plan.to_download)
assert [r.before_start for r in plan.rows if r.episode.number == 446] == [True]                                                # 기록의 가장 앞(447)보다 앞은 시작 지점 이전
print("1-3) 실패한 450은 아카이빙 뒤에도 다시 받음 OK")
# 1-4. 폴더를 비웠으면(파일 없음) 기록을 무시하고 처음부터
plan = kp.plan_by_folder_rules(eps(5), [], "작품", recorded={1, 2, 3})
assert plan.mode == "new_folder" and nums(plan.to_download) == [1, 2, 3, 4, 5] and not any(r.archived or r.downloaded for r in plan.rows)
print("1-4) 폴더 비움 → 기록 무시 OK")
# 1-5. 기록이 없으면(None) 예전 규칙 그대로: 파일 하나뿐이면 그 다음부터
plan = kp.plan_by_folder_rules(eps(404), files(400), "작품", recorded=None)
assert plan.mode == "single_marker" and plan.marker.number == 400 and nums(plan.to_download) == [401, 402, 403, 404]
print("1-5) 기록 없음 → 예전 규칙 OK")
# 1-6. 기록이 없어도 "무료는 바로" 규칙은 같다(앞에 잠긴 회차가 있어도 뒤의 무료 회차는 받는다)
plan = kp.plan_by_folder_rules(eps(8, locked=(5, 6)), files(1, 2, 3), "작품")
assert nums(plan.to_download) == [4, 7, 8] and nums(plan.locked) == [5, 6]
plan = kp.plan_by_folder_rules([*eps(3), ep(4, acc=False, hidden=True), ep(5)], files(1, 2), "작품")                           # 숨김(예정) 회차는 잠금, 받지 않음
assert nums(plan.to_download) == [3, 5] and nums(plan.locked) == [4]
print("1-6) 무료는 바로 / 숨김은 잠금 OK")

# ═══ 2. 저장소: 기록(없음 ≠ 빈 기록), 병합, 추적 중인 작품만 ═══
repository.upsert_new_kakao_webtoon(7, "기다무작품", status=repository.STATUS_ACTIVE)
assert repository.get_kakao_downloaded_numbers(7) is None                                       # 기록 없음
repository.add_kakao_downloaded_numbers(7, [3, 1]); assert repository.get_kakao_downloaded_numbers(7) is None     # 기록이 없으면 더하지 않는다(첫 자동 실행이 폴더+이력으로 만든다)
repository.set_kakao_downloaded_numbers(7, {3}); repository.add_kakao_downloaded_numbers(7, [1, 2]); repository.add_kakao_downloaded_numbers(7, [2, 3])
assert repository.get_kakao_downloaded_numbers(7) == {1, 2, 3}                                  # 병합(중복 없음)
repository.set_kakao_downloaded_numbers(7, set()); assert repository.get_kakao_downloaded_numbers(7) == set()     # 빈 기록 ≠ 기록 없음
repository.add_kakao_downloaded_numbers(999, [1]); assert repository.get_kakao_downloaded_numbers(999) is None    # 추적 안 하는 작품은 만들지 않음
repository.set_kakao_downloaded_numbers(7, {5, 6})
print("2) 저장소 OK")

# ═══ 3. 회차 저장 지점(자동·수동 공통)에서 기록 + 사용자 시나리오를 실행으로 ═══
class FakeClient:
    def __init__(self, episodes, series_item=None):
        self.episodes, self.series_item, self.fail, self.used = episodes, series_item or {"title": "기다무작품", "is_waitfree": True, "on_issue": "Y"}, set(), []
        self.ticket_ready = True
    async def list_episodes(self, series_id): return self.series_item, self.episodes
    async def viewer_image_urls(self, series_id, product_id): return None if product_id in self.fail else [f"u{product_id}"]
    async def download_image(self, url): return b"x", "image/jpeg"
    async def ticket_info(self, series_id): return kp.TicketInfo(waitfree_ready=self.ticket_ready)
    async def use_waitfree_ticket(self, product_id): self.used.append(product_id); return True, ""
    async def fetch_about(self, series_id): return None
async def no_metadata(*a, **k): return None
kp.write_series_metadata = no_metadata
FOLDER = kp.series_folder(str(KROOT), "기다무작품")
def put(*numbers):
    FOLDER.mkdir(parents=True, exist_ok=True)
    for n in numbers: (FOLDER / kp.episode_zip_name(n, f"{n}화")).write_bytes(b"zip")
def present(): return sorted(f.number for f in kp.scan_existing_files(FOLDER))
def archive_all_but_last():                      # 아카이빙: 가장 큰 번호 파일 하나만 남기고 나머지를 옮긴다
    for f in kp.scan_existing_files(FOLDER)[:-1]: (FOLDER / f.name).unlink()
def run(client, cap=50): return asyncio.run(kp.run_download(client, series_id=7, title="기다무작품", download_root=str(KROOT), max_episodes=cap))

repository.set_kakao_downloaded_numbers(7, set()); repository.set_setting("x", None)
with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET downloaded_numbers = NULL WHERE title_id = 7")        # 기록 없는 상태에서 시작(= 이 기능이 들어오기 전)
put(*F)
client = FakeClient(eps(448, locked=range(317, 364)))
r1 = run(client)
assert r1.downloaded == [448, 317] and r1.ticket_used == 317 and client.used == [317] and r1.failed is None, (r1.downloaded, r1.ticket_used)     # 무료 448을 바로, 이어서 317을 기다무로
assert {317, 448} <= repository.get_kakao_downloaded_numbers(7) and set(F) <= repository.get_kakao_downloaded_numbers(7)                        # 처음 실행에서 폴더로 기록을 만들고 받은 회차를 더함
archive_all_but_last(); assert present() == [448]                                                                                           # 아카이빙이 지나감
r2 = run(client)
assert r2.downloaded == [318] and r2.ticket_used == 318 and nums(r2.plan.locked)[0] == 318, (r2.downloaded, nums(r2.plan.locked)[:2])         # 아카이빙 뒤에도 318부터 이어서
archive_all_but_last(); r3 = run(client); assert r3.downloaded == [319]
client.ticket_ready = False; archive_all_but_last(); r4 = run(client); assert r4.downloaded == [] and r4.failed is None                       # 기다무가 충전되기 전에는 대기
print("3-1) 말씀하신 시나리오(448 바로, 317부터 순서대로, 아카이빙 뒤에도 이어서) OK")

# 3-2. 450 실패 → 아카이빙 → 다음 실행에서 450을 다시 받는다 / 실패한 회차는 기록되지 않는다
for f in list(FOLDER.iterdir()): f.unlink()
repository.set_kakao_downloaded_numbers(7, {447, 448, 449, 451}); put(447, 448, 449, 451)
client = FakeClient(eps(452)); client.fail = {450}
r = run(client); assert r.failed == 450 and r.downloaded == [] and 450 not in repository.get_kakao_downloaded_numbers(7)
archive_all_but_last(); assert present() == [451]
client.fail = set(); r = run(client); assert r.downloaded == [450, 452] and r.failed is None and {447, 448, 449, 450, 451, 452} <= repository.get_kakao_downloaded_numbers(7)
print("3-2) 실패한 450 → 아카이빙 뒤에도 다시 받음 OK")

# 3-3. 폴더를 통째로 비우면 기록을 버리고 처음부터(기록은 새로 받은 것만 남는다)
for f in list(FOLDER.iterdir()): f.unlink()
client = FakeClient(eps(3)); r = run(client)
assert r.downloaded == [1, 2, 3] and repository.get_kakao_downloaded_numbers(7) == {1, 2, 3}
print("3-3) 폴더 비움 → 처음부터 OK")

# 3-4. 수동 다운로드도 같은 지점에서 기록 + 보관 폴더로 옮겨진 회차를 다시 받아도 "교체"로 세지 않음
for f in list(FOLDER.iterdir()): f.unlink()
repository.set_kakao_downloaded_numbers(7, {1, 2, 3}); put(3)
client = FakeClient(eps(6)); client.fail = {6}
res = asyncio.run(kp.download_selected(client, series_id=7, title="기다무작품", numbers=[1, 3, 5, 6], download_root=str(KROOT)))
assert res.downloaded == [1, 3, 5] and res.failed == [6] and res.replaced == [3], (res.downloaded, res.failed, res.replaced)      # 1은 보관된 것을 다시 받은 것(교체 아님), 3은 폴더 파일 교체
assert repository.get_kakao_downloaded_numbers(7) == {1, 2, 3, 5}                                                                      # 5는 새로 기록, 실패한 6은 기록 안 함
print("3-4) 수동 다운로드 기록 OK")

# 3-5. 추적하지 않는 작품(구독 기록 없음)은 받아도 기록을 만들지 않고, 나중에 첫 자동 실행 때 폴더로 만든다
untracked = FakeClient(eps(2), {"title": "추적안함", "on_issue": "Y"})
asyncio.run(kp.run_download(untracked, series_id=888, title="추적안함", download_root=str(KROOT), max_episodes=5)); assert repository.get_kakao_downloaded_numbers(888) is None
print("3-5) 추적 안 하는 작품 OK")

# ═══ 4. 시작 시 소급(폴더 + 아카이빙 이력) ═══
def tracked(series_id, title):
    repository.upsert_new_kakao_webtoon(series_id, title, status=repository.STATUS_ACTIVE)
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET downloaded_numbers = NULL WHERE title_id = ?", (series_id,))
    return KROOT / remove_forbidden_str_kakao(title)
def touch(folder, *numbers):
    folder.mkdir(parents=True, exist_ok=True)
    for n in numbers: (folder / kp.episode_zip_name(n, f"{n}화")).write_bytes(b"zip")
touch(tracked(21, "폴더와이력"), 20, 21, 23)                                                  # 22는 폴더 안의 빈 회차(못 받은 것)
for name in ("0017_17화.zip", "0018_18화.zip", "0019_19화.zip", "엉뚱한이름.zip"): repository.add_archive_history("kakao_21", "폴더와이력", name, "periodic")   # 아카이빙으로 옮긴 것
touch(tracked(22, "표식하나"), 400)                                                           # 옛 도구가 남긴 표식 하나 — 번호가 사이트와 다를 수 있어서 첫 다운로드 때 확인한다
tracked(23, "폴더없음")
touch(tracked(24, "이미기록"), 5, 6); repository.set_kakao_downloaded_numbers(24, {1, 2, 5, 6})
touch(tracked(25, "보관만"), 90); [repository.add_archive_history("kakao_25", "보관만", f"{n:04d}_{n}화.zip", "periodic") for n in (88, 89)]    # 표식 하나 + 이력 → 기록 생성
lines = kakao_records.backfill_all(ST)
assert repository.get_kakao_downloaded_numbers(21) == {17, 18, 19, 20, 21, 23}, repository.get_kakao_downloaded_numbers(21)       # 빈 회차 22는 기록하지 않는다
assert repository.get_kakao_downloaded_numbers(22) is None and repository.get_kakao_downloaded_numbers(23) is None                 # 표식 하나/폴더 없음: 만들지 않음
assert repository.get_kakao_downloaded_numbers(24) == {1, 2, 5, 6}                                                                  # 이미 있는 기록은 건드리지 않음
assert repository.get_kakao_downloaded_numbers(25) == {88, 89, 90}
text = "\n".join(lines)
assert "폴더와이력" in text and "폴더 3개" in text and "아카이빙 이력 3개" in text and "빈 회차 1개" in text and "표식하나" in text and "첫 다운로드" in text, text
assert kakao_records.backfill_all(ST) == [l for l in kakao_records.backfill_all(ST)] and repository.get_kakao_downloaded_numbers(21) == {17, 18, 19, 20, 21, 23}     # 다시 돌려도 같다(멱등)
print("4) 소급 OK (폴더+이력, 빈 회차 제외, 표식 하나는 첫 다운로드에서, 기존 기록 유지, 멱등)")
# 4-2. 시작할 때 소급한 요약이 "실행 이력"(다운로드)에 한 건으로 남아서 설정 > 이력에서 확인할 수 있다
touch(tracked(26, "이력요약"), 30, 31, 33)
summary = kakao_records.backfill_and_log(ST)
history = [h for h in repository.list_job_history(50) if h["job_name"] == "download"]
assert any("받은 회차 기록 만들기" in str(h) and "이력요약" in str(h) and "빈 회차 1개" in str(h) for h in history), history
before = len([h for h in repository.list_job_history(50) if h["job_name"] == "download"])
repository.set_kakao_downloaded_numbers(22, {1}); kakao_records.backfill_and_log(ST)
assert len([h for h in repository.list_job_history(50) if h["job_name"] == "download"]) == before, "새로 만들 기록이 없으면 이력을 남기지 않는다"
with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET downloaded_numbers = NULL WHERE title_id = 22")      # 뒤 구간이 "22번은 기록 없음"을 기대하므로 원래대로
print("4-2) 소급 요약이 실행 이력에 남음 OK")

# ═══ 5. 분석 API: 보관 폴더로 옮겨진 회차가 "이미 받음"으로 보인다 ═══
async def fake_list(self, series_id): return {"title": "분석작품", "on_sale_count": 450, "is_waitfree": True, "on_issue": "Y", "age_grade": 15}, eps(450)
async def fake_ticket(self, series_id): return None
kp.KakaoPageClient.list_episodes = fake_list; kp.KakaoPageClient.ticket_info = fake_ticket
touch(tracked(30, "분석작품"), 447, 448, 449); repository.set_kakao_downloaded_numbers(30, set(range(440, 450)))
async def analyze():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = await c.get("/api/kakao-manual/analyze", params={"series_id": 30}); assert r.status_code == 200, r.text; return r.json()
a = asyncio.run(analyze()); e = {x["number"]: x for x in a["episodes"]}
assert e[441]["downloaded"] and e[441]["archived"] and e[448]["downloaded"] and not e[448]["archived"] and not e[450]["downloaded"], (e[441], e[448], e[450])
assert a["archived_count"] == 7 and a["downloaded_count"] == 10 and a["to_download_count"] == 1 and e[439]["before_start"], (a["archived_count"], a["downloaded_count"], a["to_download_count"])
print("5) 분석 API OK (보관 폴더로 옮긴 회차 표시)")

# ═══ 6. 백업/복원에 기록이 포함된다(새 컨테이너에서도 유지) ═══
backup = repository.export_all(); row = next(r for r in backup["kakao_webtoons"] if r["title_id"] == 21)
assert json.loads(row["downloaded_numbers"]) == [17, 18, 19, 20, 21, 23]
repository.set_kakao_downloaded_numbers(21, set()); repository.restore_all(json.loads(json.dumps(backup)))
assert repository.get_kakao_downloaded_numbers(21) == {17, 18, 19, 20, 21, 23} and repository.get_kakao_downloaded_numbers(22) is None
print("6) 백업/복원 OK")
print("\n전부 통과")
