"""아카이빙 실패 뒤 목적지에 남는 0KB 찌꺼기 처리 + **원본은 어떤 경우에도 지우지 않는다**.
배경: OneDrive는 업로드를 시작하면 0KB 빈 파일부터 만들고, 업로드가 401 등으로 실패하면 그 빈 파일을 지우는 요청도 같이 실패해서 0KB가 남는다. 앱은 "같은 이름이 이미 있다"고만 보고
충돌 정책(이름 바꿔 저장이면 "(2)", "(3)")을 적용해서 새 이름으로 또 올리고 또 실패하면서 0KB가 쌓였다(건너뜀 정책이면 그 회차가 영원히 안 옮겨졌다).
규칙: ① 크기가 0인 목적지 파일은 파일로 치지 않는다(충돌 아님, 그 이름으로 다시 올린다) ② 업로드가 실패하면 방금 남은 0KB 찌꺼기를 지운다(실패해도 이동은 그대로 실패로 처리) ③ 이동을 시작할 때 보관 폴더의 0KB .zip을 정리한다
④ 로컬 보관 폴더도 같다. **안전 규칙**: 로컬 원본은 업로드가 확인되기 전에는 지우지 않는다 / 지우는 것은 크기가 정확히 0인 목적지 파일뿐이다(크기가 있는 파일, .zip이 아닌 파일, 원본은 절대 아님) / 크기를 확인하지 못하면 아무것도 지우지 않는다."""
import tempfile
from pathlib import Path

from app import db, repository, archiver, kakao_cover
from app.archiver.params import ProgressReport, TitleArchiveJob
from app.config import get_settings
from fake_rclone import FakeRclone

db.get_connection()
st = get_settings()
A = Path(st.archive_root); D = Path(st.download_root); A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)
fake = FakeRclone().install()
cfg = Path(tempfile.mkdtemp()) / "rclone.conf"; cfg.write_text("[r]\n")
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
BASE = "r:웹툰"; repository.set_setting(archiver._DEFAULT_BASE_PATH_SETTING_KEY, BASE)            # 기본 경로면 작품별 서브폴더(웹툰/작품)

def make(title, nums, extra=None):
    d = D / title
    if d.exists():
        for p in d.iterdir(): p.unlink()
    d.mkdir(parents=True, exist_ok=True)
    for n in nums: (d / f"{n:04d}_{n}화.zip").write_bytes(f"zip{n}".encode() * 10)
    for name, data in (extra or {}).items(): (d / name).write_bytes(data)
    return d
def call(title, policy="rename", dest_type="rclone", base=BASE, keep_last=True):
    prog, conf, fail = [], [], []
    job = TitleArchiveJob(archive_root=st.archive_root, download_root=st.download_root, title_id="97", title_name=title, base_path=base, policy=policy,
                          trigger_type="manual", keep_last=keep_last, dest_type=dest_type, rclone_config_path=str(cfg) if dest_type == "rclone" else "")
    moved = archiver._archive_title(job, ProgressReport(progress_callback=prog.append, conflict_log=conf, failure_log=fail))
    return moved, prog, conf, fail
local_names = lambda d: sorted(p.name for p in Path(d).iterdir())
remote = lambda prefix: {p[len(prefix):]: len(v) for (r, p), v in fake.files.items() if r == "r" and p.startswith(prefix)}
def reset(): fake.files.clear(); fake.fail_move_names.clear(); fake.leave_empty_on_fail = False; fake.fail_delete = False; fake.deleted.clear()

# Scenario 1: 업로드가 실패해 0KB 찌꺼기가 남으면 지운다 — 원본은 그대로, 실패는 실패로 기록, 이동은 이어서
reset(); src = make("묵향", [1, 2, 3]); fake.fail_move_names.add("0001_1화.zip"); fake.leave_empty_on_fail = True
moved, prog, conf, fail = call("묵향")
assert moved == 1 and (src / "0001_1화.zip").read_bytes() == b"zip1" * 10 and not (src / "0002_2화.zip").exists(), local_names(src)      # 1화 원본 그대로, 2화는 정상 이동
assert fail and fail[0][:2] == ("묵향", "0001_1화.zip"), fail
assert "웹툰/묵향/0001_1화.zip" not in remote("") and remote("웹툰/묵향/").get("0002_2화.zip") == 40, remote("")                        # 0KB 찌꺼기는 지워졌고 2화는 올라갔다
assert fake.deleted == ["웹툰/묵향/0001_1화.zip"], fake.deleted
print("1) 실패 뒤 0KB 찌꺼기 정리 OK (원본 그대로)")

# Scenario 2: 지우기도 실패하면(401) 0KB가 남지만 — 이동이 망가지지 않고 원본은 그대로, 예외 없이 실패로만 기록된다
reset(); src = make("묵향", [1, 2, 3]); fake.fail_move_names.add("0001_1화.zip"); fake.leave_empty_on_fail = True; fake.fail_delete = True
moved, prog, conf, fail = call("묵향")
assert moved == 1 and (src / "0001_1화.zip").exists() and fail and fail[0][1] == "0001_1화.zip", (local_names(src), fail)
assert remote("웹툰/묵향/").get("0001_1화.zip") == 0, "지우지 못한 찌꺼기는 남아 있다(다음 실행에서 정리)"
print("2) 정리 실패도 안전 OK")

# Scenario 3: 다음 실행 — 남아 있는 0KB 때문에 "(2)" 같은 새 이름을 만들지 않고, 정리한 뒤 같은 이름으로 올린다. 원본은 올라간 뒤에만 사라진다
fake.fail_move_names.clear(); fake.leave_empty_on_fail = False; fake.fail_delete = False; fake.deleted.clear()
moved, prog, conf, fail = call("묵향")
r = remote("웹툰/묵향/")
assert moved == 1 and r == {"0001_1화.zip": 40, "0002_2화.zip": 40}, r                                                              # (2)/(3) 없이 같은 이름, 내용이 있다
assert conf == [] and fail == [] and not (src / "0001_1화.zip").exists() and (src / "0003_3화.zip").exists(), (conf, fail, local_names(src))
assert any("0KB" in m and "1개" in m for m in prog), prog
print("3) 재실행은 같은 이름으로 OK")

# Scenario 4: 이미 쌓인 0KB 찌꺼기("(2)", "(3)")는 이동 시작할 때 정리한다 — 크기가 있는 파일, .zip이 아닌 0KB 파일은 건드리지 않는다
reset(); src = make("묵향", [1, 2])
base_dir = "웹툰/묵향/"
for name, data in (("0015_15화.zip", b""), ("0015_15화 (2).zip", b""), ("0015_15화 (3).zip", b""), ("0014_14화.zip", b"REAL" * 5), ("메모.txt", b""), ("info.xml", b"")):
    fake.files[("r", base_dir + name)] = data
moved, prog, conf, fail = call("묵향")
r = remote(base_dir)
assert moved == 1 and "0015_15화.zip" not in r and "0015_15화 (2).zip" not in r and "0015_15화 (3).zip" not in r, r
assert r["0014_14화.zip"] == 20 and "메모.txt" in r and "info.xml" in r, f"크기 있는 파일/.zip 아닌 파일은 그대로: {r}"
assert any("0KB" in m and "3개" in m for m in prog), prog
print("4) 쌓인 찌꺼기 정리 OK (실제 파일/.zip 아닌 파일은 그대로)")

# Scenario 5: 크기가 있는 같은 이름 파일은 예전처럼 충돌이다 — 정책대로 처리(이름 바꿔 저장), 기존 파일은 그대로
reset(); src = make("묵향", [1, 2]); fake.files[("r", base_dir + "0001_1화.zip")] = b"EXISTING"
moved, prog, conf, fail = call("묵향", policy="rename")
r = remote(base_dir)
assert moved == 1 and fake.files[("r", base_dir + "0001_1화.zip")] == b"EXISTING" and r.get("0001_1화 (2).zip") == 40 and conf and conf[0][:2] == ("묵향", "0001_1화.zip"), (r, conf)
print("5) 실제 파일과의 충돌은 그대로 OK")

# Scenario 6: 건너뜀 정책 — 0KB 찌꺼기 때문에 그 회차가 영원히 안 옮겨지던 문제. 이제는 올라가고, 크기가 있는 파일은 여전히 건너뛴다
reset(); src = make("묵향", [1, 2, 3]); fake.files[("r", base_dir + "0001_1화.zip")] = b""; fake.files[("r", base_dir + "0002_2화.zip")] = b"REAL"
moved, prog, conf, fail = call("묵향", policy="skip")
assert moved == 1 and remote(base_dir) == {"0001_1화.zip": 40, "0002_2화.zip": 4} and not (src / "0001_1화.zip").exists() and (src / "0002_2화.zip").exists(), (remote(base_dir), local_names(src))
print("6) 건너뜀 정책 OK")

# Scenario 7: 크기를 확인하지 못하면(목록 조회 실패) 아무것도 지우지 않는다 — 정리도, 실패 뒤 정리도
from app import rclone_client
reset(); src = make("묵향", [1, 2]); fake.files[("r", base_dir + "0009_9화.zip")] = b""
real_purge, real_delete_empty = fake.purge_empty_zip_files, fake.delete_empty_file
def unknown(*a, **k): raise rclone_client.RcloneError("목록 조회 실패(401)")
rclone_client.purge_empty_zip_files = unknown; rclone_client.delete_empty_file = unknown; rclone_client.file_size = unknown
fake.fail_move_names.add("0001_1화.zip")
moved, prog, conf, fail = call("묵향")
assert (src / "0001_1화.zip").exists() and ("r", base_dir + "0009_9화.zip") in fake.files and fake.deleted == [], (local_names(src), fake.deleted)
fake.install()
print("7) 확인 못 하면 아무것도 안 지움 OK")

# Scenario 8: 원본 보호 — 어떤 실패 조합에서도 원본 zip은 업로드가 성공한 것만 사라진다(모든 실패 모드 반복)
for fail_delete in (False, True):
    for leave_empty in (False, True):
        reset(); fake.install(); src = make("묵향", [1, 2, 3, 4]); fake.fail_move_names.update({"0001_1화.zip", "0003_3화.zip"}); fake.leave_empty_on_fail = leave_empty; fake.fail_delete = fail_delete
        before = {p.name: p.read_bytes() for p in src.iterdir()}
        call("묵향")
        for name, data in before.items():
            on_remote = fake.files.get(("r", base_dir + name))
            assert (src / name).exists() or on_remote == data, f"원본이 사라졌는데 올라간 파일도 없다: {name} ({fail_delete}, {leave_empty})"
            if name in ("0001_1화.zip", "0003_3화.zip"): assert (src / name).read_bytes() == data, f"실패한 파일의 원본이 바뀌었다: {name}"
print("8) 원본 보호 OK")

# Scenario 9: 로컬 보관 폴더 — 0KB 파일은 충돌이 아니다(그 이름으로 옮긴다), 이동 시작할 때 0KB .zip은 정리, 크기가 있는 파일은 충돌 그대로
reset(); src = make("묵향", [1, 2, 3, 4]); dest = A / "보관L"
if dest.exists():
    for p in dest.iterdir(): p.unlink()
dest.mkdir(parents=True, exist_ok=True)
(dest / "0001_1화.zip").write_bytes(b""); (dest / "0001_1화 (2).zip").write_bytes(b""); (dest / "0002_2화.zip").write_bytes(b"REAL"); (dest / "메모.txt").write_bytes(b"")
moved, prog, conf, fail = call("묵향", policy="rename", dest_type="local", base="보관L")
names = {p.name: p.stat().st_size for p in dest.iterdir()}
assert names.get("0001_1화.zip") == 40 and "0001_1화 (2).zip" not in names and names["0002_2화.zip"] == 4 and names.get("0002_2화 (2).zip") == 40 and "메모.txt" in names, names
assert any("0KB" in m and "2개" in m for m in prog), prog
print("9) 로컬 보관 폴더 OK")

# Scenario 10: 정리가 못 지워도(지우기 실패) 0KB 찌꺼기는 충돌이 아니다 — "(2)"를 만들지 않고 건너뛰지도 않고 같은 이름으로 올라간다(덮어쓴다). 크기가 있는 파일은 그대로 충돌
for policy in ("rename", "skip"):
    reset(); fake.install(); src = make("묵향", [1, 2]); fake.files[("r", base_dir + "0001_1화.zip")] = b""; fake.fail_delete = True               # 정리 실패 상황
    moved, prog, conf, fail = call("묵향", policy=policy)
    assert moved == 1 and remote(base_dir) == {"0001_1화.zip": 40} and conf == [] and fail == [] and not (src / "0001_1화.zip").exists(), (policy, remote(base_dir), conf, fail)
# 진짜 파일과 충돌해서 이름을 바꿔야 할 때, 이름 후보 자리에 0KB 찌꺼기("(2)")가 있으면 그 자리를 쓴다("(3)"으로 넘어가지 않는다)
reset(); fake.install(); src = make("묵향", [1, 2]); fake.files[("r", base_dir + "0001_1화.zip")] = b"REAL"; fake.files[("r", base_dir + "0001_1화 (2).zip")] = b""; fake.fail_delete = True
moved, prog, conf, fail = call("묵향", policy="rename")
assert remote(base_dir) == {"0001_1화.zip": 4, "0001_1화 (2).zip": 40}, remote(base_dir)
print("10) 정리 실패 시에도 0KB는 충돌 아님 OK")

# Scenario 11: 로컬 이동 함수 자체 — 목적지의 0KB 파일은 충돌이 아니다(skip이어도 옮기고 rename이어도 새 이름을 안 만든다), 크기 있는 파일은 충돌. 옮기다 실패하면 원본은 그대로이고 새로 생긴 빈 파일만 정리한다
from app.archiver import transfer
d = Path(tempfile.mkdtemp()); out = Path(tempfile.mkdtemp())
for policy in ("skip", "rename", "overwrite"):
    (out / "a.zip").write_bytes(b""); s = d / "a.zip"; s.write_bytes(b"DATA")
    name, conflict = transfer.move_file_with_conflict_policy(s, out, policy)
    assert name == "a.zip" and conflict is False and (out / "a.zip").read_bytes() == b"DATA" and not s.exists(), (policy, name, conflict, sorted(x.name for x in out.iterdir()))
    (out / "a.zip").unlink()
(out / "b.zip").write_bytes(b"REAL"); s = d / "b.zip"; s.write_bytes(b"NEW")
assert transfer.move_file_with_conflict_policy(s, out, "skip") == (None, True) and s.exists() and (out / "b.zip").read_bytes() == b"REAL"
(out / "b (2).zip").write_bytes(b"")                                                                                          # 이름 후보 자리의 0KB 찌꺼기는 그 자리를 쓴다
name, conflict = transfer.move_file_with_conflict_policy(s, out, "rename"); assert name == "b (2).zip" and conflict is True and (out / "b.zip").read_bytes() == b"REAL" and (out / "b (2).zip").read_bytes() == b"NEW" and not (out / "b (3).zip").exists(), sorted(x.name for x in out.iterdir())
real_move = transfer.shutil.move
def failing_move(src_, dst_):
    Path(dst_).write_bytes(b"")                                      # 복사를 시작하다 실패하는 상황: 빈 파일이 생기고 오류
    raise OSError("디스크 오류")
transfer.shutil.move = failing_move
s = d / "c.zip"; s.write_bytes(b"CCC")
try: transfer.move_file_with_conflict_policy(s, out, "rename"); raise AssertionError("실패해야 한다")
except OSError: pass
transfer.shutil.move = real_move
assert s.read_bytes() == b"CCC" and not (out / "c.zip").exists(), "원본은 그대로, 새로 생긴 빈 파일만 정리"
(out / "d.zip").write_bytes(b"REAL"); s = d / "d.zip"; s.write_bytes(b"DDD")                                      # 같은 이름의 진짜 파일이 있을 때 실패해도 그 파일은 안 지운다(크기가 있다)
def failing_move2(src_, dst_): raise OSError("디스크 오류")
transfer.shutil.move = failing_move2
try: transfer.move_file_with_conflict_policy(s, out, "overwrite"); raise AssertionError("실패해야 한다")
except OSError: pass
transfer.shutil.move = real_move
assert s.read_bytes() == b"DDD" and (out / "d.zip").read_bytes() == b"REAL"
print("11) 로컬 이동 함수 OK")
print("ALL OK")
