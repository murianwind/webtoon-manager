"""완결 전체 이동이 끝나면 그 웹툰(폴더)의 아카이빙 대상 설정을 삭제한다 — 완결 자동이동(구독해제 대기열)과 수동 실행의 "완결 처리로 이동" 둘 다.

- 삭제 조건: 이동이 문제없이(실패 없이) 끝났고 다운로드 쪽 폴더가 비어서 정리됐을 때만. 일부 실패했거나 파일이 남았거나 설정이 모자라 못 옮겼으면 설정을 남겨서 다시 시도할 수 있게 한다.
- 삭제되면 아카이빙 대상 목록과 아카이빙 수동 실행 목록에서 사라진다(둘 다 이 대상 목록에서 만든다).
- 마지막 파일을 남기는 일반(정기/수동) 이동은 설정을 건드리지 않는다. 대상 설정이 없는 웹툰(기본 보관 폴더로 가는 경우)은 삭제할 게 없어서 그대로 동작한다.
"""
import shutil
import tempfile
from pathlib import Path

from app import archiver, db, repository
from app.config import get_settings
from fake_rclone import FakeRclone
db.get_connection()

st = get_settings(); A = Path(st.archive_root); D = Path(st.download_root); K = Path(tempfile.mkdtemp())
A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)
FakeRclone().install()
repository.set_setting(archiver.settings._ON_FINISH_UNSUBSCRIBE_SETTING_KEY, "1")


def make(root, title, nums=(1, 2, 3), extra=None):
    d = root / title
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    for n in nums: (d / f"{n:04d} {n}화.zip").write_bytes(f"zip{n}".encode())
    (d / "info.xml").write_text("<ComicInfo/>", encoding="utf-8"); (d / "cover.jpg").write_bytes(b"C")
    for name in (extra or []): (d / name).write_text("남의 파일", encoding="utf-8")
    return d


def webtoon(title_id, title, base, enabled=True):
    repository.upsert_new(title_id, title)
    repository.set_status(title_id, repository.STATUS_UNSUBSCRIBED)
    repository.upsert_archive_target(title_id, base, enabled=enabled)


def pending(title_id): repository.add_pending_finish_archive(title_id)
def process(prog, fail=None): return archiver.process_pending_finish_archives(st.archive_root, st.download_root, "", prog.append, None, fail)
def target_ids(): return [t.title_id for t in repository.list_archive_targets()]


# Scenario 1: 완결 자동이동(대기열) — 다 옮기고 폴더가 정리되면 대상 설정이 삭제되고 대기열에서도 빠진다
src = make(D, "완결작1"); webtoon("901", "완결작1", "보관901"); pending("901")
prog = []; moved = process(prog)
assert moved == 3 and not src.exists() and sorted(p.name for p in (A / "보관901").iterdir()) == ["0001 1화.zip", "0002 2화.zip", "0003 3화.zip", "cover.jpg", "info.xml"]
assert repository.get_archive_target("901") is None and "901" not in target_ids() and "901" not in repository.list_pending_finish_archive()
assert any("완결작1" in line and "아카이빙 대상에서 삭제" in line for line in prog), prog
print("1) 대기열 완결 이동 → 대상 삭제 OK")

# Scenario 2: 파일 하나가 이동에 실패하면 설정을 남긴다(같은 작품을 다시 시도할 수 있게) — 실패는 호출부의 실패 목록에도 그대로 전달된다
src = make(D, "완결작2"); webtoon("902", "완결작2", "보관902"); pending("902")
real = archiver.title_archive._move_episode_file
def flaky(dest_type, s, name, policy, *a):
    if s.name.startswith("0002"): raise OSError("디스크 오류")
    return real(dest_type, s, name, policy, *a)
archiver.title_archive._move_episode_file = flaky
fail = []; prog = []
try: process(prog, fail)
finally: archiver.title_archive._move_episode_file = real
assert src.exists() and (src / "0002 2화.zip").exists() and repository.get_archive_target("902") is not None, "실패했는데 설정이 삭제됨"
assert fail == [("완결작2", "0002 2화.zip", "디스크 오류")] and not any("아카이빙 대상에서 삭제" in line for line in prog)
print("2) 일부 실패 → 설정 유지 OK")

# Scenario 3: 다운로드 폴더에 남의 파일이 남아 폴더가 정리되지 않으면 설정을 남긴다
src = make(D, "완결작3", extra=["메모.txt"]); webtoon("903", "완결작3", "보관903"); pending("903")
process([])
assert src.exists() and (src / "메모.txt").exists() and repository.get_archive_target("903") is not None
print("3) 폴더가 남음 → 설정 유지 OK")

# Scenario 4: 보관 위치가 설정되지 않아 아무것도 못 옮겼으면 설정과 파일을 그대로 둔다
src = make(D, "완결작4"); webtoon("904", "완결작4", "보관904"); pending("904")
moved = archiver.process_pending_finish_archives("", st.download_root, "", None, None, None)       # ARCHIVE_ROOT 없음
assert moved == 0 and len(list(src.iterdir())) == 5 and repository.get_archive_target("904") is not None
print("4) 보관 위치 없음 → 설정 유지 OK")

# Scenario 5: 대상 설정이 없는 웹툰은 기본 보관 폴더로 옮기고, 지울 설정이 없어도 문제없이 끝난다
src = make(D, "완결작5"); repository.upsert_new("905", "완결작5"); repository.set_status("905", repository.STATUS_UNSUBSCRIBED)
repository.set_setting(archiver.settings._DEFAULT_BASE_PATH_SETTING_KEY, "기본보관"); repository.set_setting(archiver.settings._DEFAULT_DEST_TYPE_SETTING_KEY, "local"); pending("905")
moved = process([])
assert moved == 3 and not src.exists() and (A / "기본보관" / "완결작5" / "0003 3화.zip").exists() and repository.get_archive_target("905") is None   # 기본 그릇 폴더 밑에 작품 폴더로
print("5) 대상 없음(기본 폴더) OK")

# Scenario 6: 수동 "완결 처리로 이동"(full_move) — 끝나면 삭제, 마지막 파일을 남기는 일반 수동 이동은 설정을 그대로 둔다
src = make(D, "완결작6"); webtoon("906", "완결작6", "보관906")
moved = archiver.manual_archive_now(st.archive_root, st.download_root, ["906"], "", None, False)
assert moved == 2 and src.exists() and repository.get_archive_target("906") is not None                      # 일반 수동: 마지막 파일 보존, 설정 유지
prog = []; moved = archiver.manual_archive_now(st.archive_root, st.download_root, ["906"], "", prog.append, True)
assert not src.exists() and repository.get_archive_target("906") is None and "906" not in target_ids() and any("아카이빙 대상에서 삭제" in line for line in prog), prog
print("6) 수동 완결 처리 → 삭제 OK")
# 수동 완결 처리 중 일부 실패 → 설정 유지
src = make(D, "완결작7"); webtoon("907", "완결작7", "보관907")
archiver.title_archive._move_episode_file = flaky
try: archiver.manual_archive_now(st.archive_root, st.download_root, ["907"], "", None, True)
finally: archiver.title_archive._move_episode_file = real
assert src.exists() and repository.get_archive_target("907") is not None
print("7) 수동 완결 처리 일부 실패 → 유지 OK")

# 수동 완결 처리에서도 다운로드 폴더에 남의 파일이 남아 폴더가 정리되지 않으면 설정을 남긴다
src = make(D, "완결작7b", extra=["메모.txt"]); webtoon("9071", "완결작7b", "보관9071")
archiver.manual_archive_now(st.archive_root, st.download_root, ["9071"], "", None, True)
assert src.exists() and (src / "메모.txt").exists() and repository.get_archive_target("9071") is not None
print("7b) 수동 완결 처리, 폴더가 남음 → 유지 OK")

# Scenario 8: 카카오페이지 작품("kakao_<번호>")도 같다 — 카카오 다운로드 폴더에서 옮기고 끝나면 삭제
repository.upsert_new_kakao_webtoon(12345, "카카오 완결작", status=repository.STATUS_UNSUBSCRIBED)
ksrc = make(K, "카카오 완결작"); repository.upsert_archive_target("kakao_12345", "보관카카오", enabled=True)
archiver.manual_archive_now(st.archive_root, st.download_root, ["kakao_12345"], "", None, True, str(K))
assert not ksrc.exists() and repository.get_archive_target("kakao_12345") is None and (A / "보관카카오" / "0001 1화.zip").exists()
print("8) 카카오 작품 OK")

# Scenario 9: 폴더 대상(원본 폴더 → 보관 폴더)의 완결 처리 — 끝나면 삭제, 실패하면 유지
srcf = A / "원본폴더9"; shutil.rmtree(srcf, ignore_errors=True); srcf.mkdir(parents=True)
for n in (1, 2): (srcf / f"{n:04d}_{n}화.zip").write_bytes(b"x")
(srcf / "info.xml").write_text("<ComicInfo/>")
fid = repository.create_folder_archive_target("원본폴더9", "local", "원본폴더9", "대상폴더9", "local", True)
archiver.manual_archive_now(st.archive_root, st.download_root, [fid], "", None, False)                           # 일반 수동: 설정 유지
assert repository.get_archive_target(fid) is not None
prog = []; archiver.manual_archive_now(st.archive_root, st.download_root, [fid], "", prog.append, True)
assert not srcf.exists() and repository.get_archive_target(fid) is None and fid not in target_ids() and any("아카이빙 대상에서 삭제" in line for line in prog), prog
srcf2 = A / "원본폴더10"; shutil.rmtree(srcf2, ignore_errors=True); srcf2.mkdir(parents=True); (srcf2 / "0001_1화.zip").write_bytes(b"x"); (srcf2 / "0002_2화.zip").write_bytes(b"y")
fid2 = repository.create_folder_archive_target("원본폴더10", "local", "원본폴더10", "대상폴더10", "local", True)
real_single = archiver.folder_archive._bulk_move_single_file
def flaky_single(**kw):
    if kw["rel_path"].startswith("0002"): raise OSError("디스크 오류")
    return real_single(**kw)
archiver.folder_archive._bulk_move_single_file = flaky_single
try: archiver.manual_archive_now(st.archive_root, st.download_root, [fid2], "", None, True)
finally: archiver.folder_archive._bulk_move_single_file = real_single
assert srcf2.exists() and repository.get_archive_target(fid2) is not None
print("9) 폴더 대상 OK")
print("ALL OK")
