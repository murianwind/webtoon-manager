"""카카오 "받은 회차 기록"은 폴더가 비어도(없어져도) 유지된다 — 전체를 아카이빙해서 폴더가 없는 작품이 연재를 재개해도 새 회차부터 받는다.

- 계획: 폴더에 파일이 없어도 기록이 있으면 기록에 없는 회차만 받는다(mode "recorded_only"). 기록이 없으면 예전처럼 처음부터(new_folder).
- 기록은 폴더 상태로 지워지지 않는다(sync_before_planning). 날짜로 지워지는 아카이빙 이력에 기대지 않도록, 아카이빙으로 옮기기 직전에 폴더의 회차를 기록에 확정해 둔다.
- 폴더 없이 "받은 것으로 기록"해도 분석에 그대로 나오고, 새 회차를 받을 때 폴더가 다시 만들어진다. 처음부터 다시 받으려면 "받지 않은 것으로 기록"(받을 대상 지정)을 쓴다.
"""
import asyncio
import tempfile
from pathlib import Path

from app import db, kakao_page_download as kp, kakao_records, repository
from app.archiver import _archive_title
from app.archiver.params import ProgressReport, TitleArchiveJob
from app.config import get_settings
db.get_connection()

ST = get_settings()
KROOT = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(KROOT))
SID, TITLE = 31, "빈폴더작품"
repository.upsert_new_kakao_webtoon(SID, TITLE, status=repository.STATUS_ACTIVE)
FOLDER = kp.series_folder(str(KROOT), TITLE)


def ep(n, acc=True): return kp.Episode(product_id=n, title=f"{TITLE} {n}화", number=n, subtitle=f"{n}화", is_free=acc, accessible=acc, page_count=1, hidden=False)
def eps(total, locked=()): return [ep(n, acc=n not in locked) for n in range(1, total + 1)]
def files(*numbers): return [kp.ExistingFile(number=n, subtitle=f"{n}화", name=f"{n:04d}_{n}화.zip") for n in numbers]
def nums(rows): return [e.number for e in rows]
def record(): return repository.get_kakao_downloaded_numbers(SID)
def reset_record(value=None):
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET downloaded_numbers = NULL, pending_numbers = NULL WHERE title_id = ?", (SID,))
    if value is not None: repository.set_kakao_downloaded_numbers(SID, value)


# Scenario 1: 계획 — 폴더가 비어도 기록이 있으면 기록에 없는 회차만 받는다. 기록 앞 번호는 시작 지점 이전, 기록 회차는 "폴더에 없음"(archived)으로 표시
plan = kp.plan_by_folder_rules(eps(6), [], TITLE, recorded={1, 2, 3, 4, 5})
assert plan.mode == "recorded_only" and plan.existing_count == 0 and nums(plan.to_download) == [6] and plan.locked == []
assert all(r.downloaded and r.archived for r in plan.rows if r.episode.number <= 5) and not any(r.before_start for r in plan.rows)
plan = kp.plan_by_folder_rules(eps(6, locked=(6,)), [], TITLE, recorded={1, 2, 3, 4, 5})
assert plan.to_download == [] and nums(plan.locked) == [6]
# 기록 앞 번호는 시작 지점 이전(자동으로 받지 않음), "받지 않은 것으로" 지정한 회차는 시작 지점 앞이어도 받는다
plan = kp.plan_by_folder_rules(eps(6), [], TITLE, recorded={4, 5}, pending={2})
assert nums(plan.to_download) == [2, 6] and [r.before_start for r in plan.rows if r.episode.number in (1, 3)] == [True, True]
# 기록이 없거나 비어 있으면 예전처럼 처음부터(새 작품/기록 없음)
for empty in (None, set()):
    plan = kp.plan_by_folder_rules(eps(3), [], TITLE, recorded=empty)
    assert plan.mode == "new_folder" and nums(plan.to_download) == [1, 2, 3]
print("1) 계획 OK")

# Scenario 2: 폴더가 비어도 기록을 지우지 않는다(예전에는 비우면 기록을 버렸다). 기록이 없으면 그대로 None
reset_record({1, 2, 3, 4, 5})
got = kakao_records.sync_before_planning(SID, [], kp.plan_by_folder_rules(eps(5), [], TITLE))
assert got == {1, 2, 3, 4, 5} and record() == {1, 2, 3, 4, 5}
reset_record()
assert kakao_records.sync_before_planning(SID, [], kp.plan_by_folder_rules(eps(5), [], TITLE)) is None and record() is None
print("2) 폴더가 비어도 기록 유지 OK")

# Scenario 3: 폴더 없이 전체를 받은 것으로 기록 → 분석(계획)에 전부 받은 것으로 나오고 받을 회차는 없다(폴더를 만들거나 파일을 넣을 필요가 없다)
reset_record()
result = kakao_records.mark(SID, range(1, 6), True, [])
assert result["marked"] == [1, 2, 3, 4, 5] and record() == {1, 2, 3, 4, 5}
plan = kp.plan_by_folder_rules(eps(5, locked=()), [], TITLE, record(), repository.get_kakao_pending_numbers(SID))
assert all(r.downloaded for r in plan.rows) and plan.to_download == [] and plan.locked == [] and plan.mode == "recorded_only"
print("3) 폴더 없이 받은 것으로 기록 OK")


# Scenario 4: 실제 실행 — 기록 {1~5}, 폴더 없음, 사이트에 6화가 새로 올라오면 6화만 받고 폴더가 그때 만들어진다
class FakeClient:
    def __init__(self, episodes):
        self.episodes, self.series_item, self.used, self.ticket_ready = episodes, {"title": TITLE, "is_waitfree": True, "on_issue": "Y"}, [], False
    async def list_episodes(self, series_id): return self.series_item, self.episodes
    async def viewer_image_urls(self, series_id, product_id): return [f"u{product_id}"]
    async def download_image(self, url): return b"x", "image/jpeg"
    async def ticket_info(self, series_id): return kp.TicketInfo(waitfree_ready=self.ticket_ready)
    async def use_waitfree_ticket(self, product_id): self.used.append(product_id); return True, ""
    async def fetch_about(self, series_id): return None
async def no_metadata(*a, **k): return None
kp.runner.write_series_metadata = no_metadata
def run(client): return asyncio.run(kp.run_download(client, series_id=SID, title=TITLE, download_root=str(KROOT), max_episodes=50))
def present(): return sorted(f.number for f in kp.scan_existing_files(FOLDER)) if FOLDER.exists() else None

reset_record({1, 2, 3, 4, 5}); assert not FOLDER.exists()
r = run(FakeClient(eps(5)))
assert r.downloaded == [] and not FOLDER.exists() and record() == {1, 2, 3, 4, 5}, (r.downloaded, FOLDER.exists())          # 새 회차가 없으면 폴더도 만들지 않는다
r = run(FakeClient(eps(6, locked=(6,))))
assert r.downloaded == [] and not FOLDER.exists() and nums(r.plan.locked) == [6]                                           # 새 회차가 아직 잠겨 있으면(기다무도 없음) 기다린다
r = run(FakeClient(eps(6)))
assert r.downloaded == [6] and present() == [6] and record() == {1, 2, 3, 4, 5, 6}, (r.downloaded, present(), record())     # 열리면 6화부터, 폴더는 그때 만들어진다
# 폴더를 지워도(다시 아카이빙해도) 기록은 그대로라 7화만 받는다
for f in FOLDER.iterdir(): f.unlink()
FOLDER.rmdir()
r = run(FakeClient(eps(7)))
assert r.downloaded == [7] and present() == [7] and record() == set(range(1, 8)), (r.downloaded, present(), record())
print("4) 새 회차부터 받고 폴더를 다시 만듦 OK")

# Scenario 5: 처음부터 다시 받고 싶으면 폴더를 비우는 대신 "받지 않은 것으로 기록" — 전부 받을 대상으로 지정되어 폴더가 없어도 1화부터 받는다
for f in FOLDER.iterdir(): f.unlink()
FOLDER.rmdir()
kakao_records.mark(SID, range(1, 8), False, [])
assert record() == set() and repository.get_kakao_pending_numbers(SID) == set(range(1, 8))
r = run(FakeClient(eps(7)))
assert r.downloaded == list(range(1, 8)) and present() == list(range(1, 8)) and repository.get_kakao_pending_numbers(SID) == set()
print("5) 받지 않은 것으로 기록 → 처음부터 OK")

# Scenario 6: 아카이빙으로 옮기기 직전에 폴더의 회차를 기록에 확정한다 — 이력이 지워져도(보관 기간) 기록이 남는다
ARCH = Path(ST.archive_root); ARCH.mkdir(parents=True, exist_ok=True)
def make_folder(*numbers):
    FOLDER.mkdir(parents=True, exist_ok=True)
    for f in list(FOLDER.iterdir()): f.unlink()
    for n in numbers: (FOLDER / kp.episode_zip_name(n, f"{n}화")).write_bytes(b"zip")
def archive(keep_last):
    job = TitleArchiveJob(archive_root=str(ARCH), download_root=str(KROOT), title_id=f"kakao_{SID}", title_name=TITLE, base_path="보관31", policy="skip",
                          trigger_type="manual" if keep_last else "finish_unsubscribe", keep_last=keep_last, kakao=True)
    return _archive_title(job, ProgressReport())
def purge_archive_history():
    with db.write_transaction() as cx: cx.execute("DELETE FROM archive_history")
# (a) 기록이 없는 작품을 완결 처리(전체 이동)로 옮기면 폴더 회차가 기록으로 남는다. 이력을 지워도 기록은 그대로
reset_record(); make_folder(1, 2, 3, 4)
assert archive(keep_last=False) == 4 and record() == {1, 2, 3, 4}, record()
purge_archive_history(); assert kakao_records.archived_numbers(SID) == set() and record() == {1, 2, 3, 4}
for f in FOLDER.parent.glob(f"{FOLDER.name}*"):
    for x in f.iterdir(): x.unlink()
    f.rmdir()
plan = kp.plan_by_folder_rules(eps(5), [], TITLE, record(), set())
assert plan.mode == "recorded_only" and nums(plan.to_download) == [5] and all(r.downloaded for r in plan.rows if r.episode.number <= 4)
# (b) 정기 아카이빙(마지막 파일 남기기)도 남기는 파일까지 포함해 폴더의 회차를 모두 기록한다 — 기존 기록과 합친다
reset_record({10}); make_folder(11, 12, 13)
assert archive(keep_last=True) == 2 and record() == {10, 11, 12, 13}, record()
# (b2) 기록이 아직 없는 작품은 이미 옮겨 둔 회차(아카이빙 이력)와 폴더 회차를 합쳐서 처음 기록을 만든다
purge_archive_history(); reset_record()
repository.add_archive_history(f"kakao_{SID}", TITLE, "0005_5화.zip", "periodic"); repository.add_archive_history(f"kakao_{SID}", TITLE, "0006_6화.zip", "periodic")
make_folder(7, 8)                                                          # 앞 시나리오에서 보관 폴더로 옮긴 번호와 겹치지 않게(겹치면 같은 이름 건너뛰기로 옮겨지지 않는다)
assert archive(keep_last=True) == 1 and record() == {5, 6, 7, 8}, record()
# (c) 아카이빙할 파일이 없거나 보관 설정이 모자라 못 옮기면 기록을 건드리지 않는다
reset_record({10}); make_folder(11, 12)
real_archive_root = str(ARCH)
job = TitleArchiveJob(archive_root="", download_root=str(KROOT), title_id=f"kakao_{SID}", title_name=TITLE, base_path="보관31", policy="skip", trigger_type="manual", keep_last=False, kakao=True)
assert _archive_title(job, ProgressReport()) == 0 and record() == {10}
print("6) 아카이빙 전 기록 확정 OK")
print("ALL OK")
