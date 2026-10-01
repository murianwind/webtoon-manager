"""폴더 대상 아카이빙(_archive_folder_target)과 일괄 이동(bulk_move_folder) 특성 테스트 — 로컬/rclone 네 조합, 충돌 정책, 표지 갱신, 설정 오류, 파일 하나 실패."""
import io, tempfile, zipfile
from pathlib import Path
from types import SimpleNamespace as NS

from app import db, repository, archiver, kakao_cover, rclone_client
from app.config import get_settings
from fake_rclone import FakeRclone

db.get_connection()
st = get_settings(); A = Path(st.archive_root); A.mkdir(parents=True, exist_ok=True)
fake = FakeRclone().install(); CFG = "fake.conf"
archiver._get_effective_filename_template = lambda preset_id: TEMPLATE["v"]; TEMPLATE = {"v": ""}

def mk(rel, files):
    d = A / rel
    if d.exists():
        for p in sorted(d.rglob("*"), reverse=True): p.unlink() if p.is_file() else p.rmdir()
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True); (d / name).write_bytes(data if isinstance(data, bytes) else data.encode())
    return d
def names(d): return sorted(str(p.relative_to(d)) for p in Path(d).rglob("*") if p.is_file())
def zip_with(n_pages):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(n_pages): z.writestr(f"{i}.jpg", b"x")
    return buf.getvalue()
def target(src_type, src, dest_type, dest, **kw): return NS(display_name=kw.get("display_name", ""), source_dest_type=src_type, source_path=src, dest_type=dest_type, dest_base_path=dest, filename_template_preset_id=None, title_id=kw.get("title_id", "folder_x"))
def run(t, policy="skip", keep_last=True):
    prog, conf, fail = [], [], []
    moved = archiver._archive_folder_target(t, policy, CFG, st.archive_root, keep_last, prog.append, conf, fail)
    return moved, prog, conf, fail
def hist(title_id): return sorted(r["file_name"] for r in repository.list_archive_history(1, 500)[0] if r["title_id"] == title_id)
def remote(prefix=""): return sorted(p for (r, p) in fake.files if r == "r" and p.startswith(prefix))

# ═══ _archive_folder_target ═══
# 1. 로컬→로컬(마지막 회차 보존): 정보 파일은 복사, 이력은 대상 title_id/폴더 이름으로
src = mk("원본1", {"0001_1화.zip": "a", "0002_2화.zip": "b", "0003_3화.zip": "c", "info.xml": "<x/>", "cover.jpg": "C"})
moved, prog, conf, fail = run(target("local", "원본1", "local", "대상1", title_id="folder_1"))
assert moved == 2 and names(src) == ["0003_3화.zip", "cover.jpg", "info.xml"] and names(A / "대상1") == ["0001_1화.zip", "0002_2화.zip", "cover.jpg", "info.xml"]
assert hist("folder_1") == ["0001_1화.zip", "0002_2화.zip"] and "[원본1] 이동 완료: 0001_1화.zip" in prog and conf == [] and fail == []
assert repository.list_archive_history(1, 500)[0][0]["title_name"] == "원본1"                                    # 표시 이름은 원본 폴더 이름에서
print("F1) 로컬→로컬 OK")

# 2. 충돌 정책 (skip / rename / overwrite — overwrite는 충돌로 기록하지 않는다)
for policy, expect, moved_n, conf_n in (("skip", ["0001_1화.zip", "0002_2화.zip"], 1, 1), ("rename", ["0001_1화 (2).zip", "0001_1화.zip", "0002_2화.zip"], 2, 1), ("overwrite", ["0001_1화.zip", "0002_2화.zip"], 2, 0)):
    mk("원본2", {"0001_1화.zip": "new1", "0002_2화.zip": "new2", "0003_3화.zip": "z"}); mk("대상2", {"0001_1화.zip": "OLD"})
    moved, prog, conf, fail = run(target("local", "원본2", "local", "대상2"), policy=policy)
    assert moved == moved_n and names(A / "대상2") == expect and len(conf) == conf_n, (policy, moved, names(A / "대상2"), conf)
    if policy == "skip": assert "[원본2] 건너뜀(이미 존재): 0001_1화.zip" in prog and conf == [("원본2", "0001_1화.zip", "skip")]
    if policy == "overwrite": assert (A / "대상2" / "0001_1화.zip").read_bytes() == b"new1"
print("F2) 충돌 정책 OK")

# 3. 원본 폴더가 없으면 실패 기록
moved, prog, conf, fail = run(target("local", "없는원본", "local", "대상3")); assert moved == 0 and fail == [("없는원본", "-", "원본 폴더가 존재하지 않습니다.")]
print("F3) 원본 없음 OK")

# 4. 완결 처리(keep_last=False): 전부 이동 + 정보 파일 이동 + 원본 폴더 정리 + 카카오 표지 갱신 안내, 이력은 manual_finish
src = mk("원본4", {"0001_1화.zip": "a", "0002_2화.zip": "b", "info.xml": "<x/>", "cover.jpg": "C"})
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: True
moved, prog, conf, fail = run(target("local", "원본4", "local", "대상4", title_id="folder_4"), keep_last=False)
assert moved == 2 and not src.exists() and names(A / "대상4") == ["0001_1화.zip", "0002_2화.zip", "cover.jpg", "info.xml"] and "[원본4] 카카오 표지 갱신함" in prog
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
print("F4) 완결 처리 OK")

# 5. 로컬→rclone (정보 파일 복사)
mk("원본5", {"0001_1화.zip": "a", "0002_2화.zip": "b", "0003_3화.zip": "c", "info.xml": "<x/>"})
moved, prog, conf, fail = run(target("local", "원본5", "rclone", "r:웹툰/대상5"))
assert moved == 2 and remote("웹툰/대상5") == ["웹툰/대상5/0001_1화.zip", "웹툰/대상5/0002_2화.zip", "웹툰/대상5/info.xml"] and ("r", "웹툰/대상5") in fake.folders
assert names(A / "원본5") == ["0003_3화.zip", "info.xml"]
print("F5) 로컬→rclone OK")

# 6. rclone→로컬: 원격 파일 목록에서 zip만 번호순, 마지막 보존, 정보 파일 복사(원격 원본 유지)
fake.files.clear()
for n, data in (("0001_1화.zip", b"a"), ("0002_2화.zip", b"b"), ("0003_3화.zip", b"c"), ("info.xml", b"<x/>"), ("cover.png", b"P"), ("memo.txt", b"m")): fake.files[("r", f"원격원본/{n}")] = data
moved, prog, conf, fail = run(target("rclone", "r:원격원본", "local", "대상6"))
assert moved == 2 and names(A / "대상6") == ["0001_1화.zip", "0002_2화.zip", "cover.png", "info.xml"] and remote("원격원본") == ["원격원본/0003_3화.zip", "원격원본/cover.png", "원격원본/info.xml", "원격원본/memo.txt"]
assert "[원격원본] 이동 완료: 0001_1화.zip" in prog and (A / "대상6" / "0001_1화.zip").read_bytes() == b"a"
print("F6) rclone→로컬 OK")

# 7. 잘못된 원격 정보(원격 이름이 비어 있을 때 — 콜론이 없으면 전체를 원격 이름으로 본다) / 목록 조회 실패 / 목적지 원격 오류
moved, prog, conf, fail = run(target("rclone", ":경로", "local", "대상7")); assert moved == 0 and fail == [("경로", "-", "원본 원격 정보가 올바르지 않습니다.")]
orig_list = rclone_client.list_top_level_files
def bad_list(*a): raise rclone_client.RcloneError("연결 실패")
rclone_client.list_top_level_files = bad_list
moved, prog, conf, fail = run(target("rclone", "r:원격원본", "local", "대상7")); assert moved == 0 and fail == [("원격원본", "-", "원본 원격 폴더 목록 조회 실패: 연결 실패")]
rclone_client.list_top_level_files = orig_list
mk("원본7", {"0001_1화.zip": "a", "0002_2화.zip": "b"})
moved, prog, conf, fail = run(target("local", "원본7", "rclone", ":경로")); assert moved == 0 and fail == [("원본7", "-", "목적지 원격 정보가 올바르지 않습니다.")] and names(A / "원본7") == ["0001_1화.zip", "0002_2화.zip"]
print("F7) 설정 오류 OK")

# 8. 파일 하나가 실패해도 나머지는 계속
mk("원본8", {"0001_1화.zip": "a", "0002_2화.zip": "b", "0003_3화.zip": "c", "0004_4화.zip": "d"})
orig_single = archiver._bulk_move_single_file
def flaky(**kw):
    if kw["rel_path"].startswith("0002"): raise OSError("디스크 오류")
    return orig_single(**kw)
archiver._bulk_move_single_file = flaky
moved, prog, conf, fail = run(target("local", "원본8", "local", "대상8")); archiver._bulk_move_single_file = orig_single
assert moved == 2 and fail == [("원본8", "0002_2화.zip", "디스크 오류")] and "[원본8] 이동 실패: 0002_2화.zip — 디스크 오류" in prog and names(A / "대상8") == ["0001_1화.zip", "0003_3화.zip"]
print("F8) 파일 실패 격리 OK")

# 9. 파일명 템플릿: 로컬 원본은 zip 페이지 수까지, 원격 원본은 {page_count}면 원래 이름 유지
mk("원본9", {"0001_1화.zip": zip_with(2), "0002_2화.zip": zip_with(3), "0003_3화.zip": zip_with(1)})
TEMPLATE["v"] = "{page_count}p {subtitle}"
moved, prog, conf, fail = run(target("local", "원본9", "local", "대상9")); assert names(A / "대상9") == ["2p 1화.zip", "3p 2화.zip"], names(A / "대상9")
fake.files.clear(); [fake.files.__setitem__(("r", f"원격9/{n}"), b"z") for n in ("0001_1화.zip", "0002_2화.zip", "0003_3화.zip")]
moved, prog, conf, fail = run(target("rclone", "r:원격9", "local", "대상9b")); assert names(A / "대상9b") == ["0001_1화.zip", "0002_2화.zip"]      # 원격이라 페이지 수를 못 세서 이름 유지
TEMPLATE["v"] = ""
print("F9) 파일명 템플릿 OK")

# 10. 원격→원격 완결 처리 + 원격 원본의 카카오 표지 갱신(info.xml의 <Web>으로 판별 → 임시 표지를 목적지 cover.jpg로 올림)
fake.files.clear()
for n, data in (("0001_1화.zip", b"a"), ("0002_2화.zip", b"b"), ("info.xml", b'<ComicInfo><Web>https://page.kakao.com/content/555</Web></ComicInfo>'), ("cover.png", b"OLD")): fake.files[("r", f"카카오원본/{n}")] = data
tmp_cover = Path(tempfile.mkdtemp()) / "c.jpg"; tmp_cover.write_bytes(b"NEWCOVER")
archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp_cover) if sid == "555" else None
moved, prog, conf, fail = run(target("rclone", "r:카카오원본", "rclone", "r:완결/카카오"), keep_last=False)
assert moved == 2 and fake.files[("r", "완결/카카오/cover.jpg")] == b"NEWCOVER" and "[카카오원본] 카카오 표지 갱신함" in prog and not tmp_cover.exists()      # 임시 파일 정리
assert remote("카카오원본") == [] and ("r", "완결/카카오/0001_1화.zip") in fake.files and ("r", "완결/카카오/info.xml") in fake.files
print("F10) 원격→원격 + 표지 갱신 OK")

# 11. 표지 갱신 중 예외는 무시하고 이동은 계속 / 옮길 게 하나도 없으면 0
def boom_refresh(d, **k): raise RuntimeError("표지 오류")
kakao_cover.refresh_kakao_cover_if_applicable = boom_refresh
mk("원본11", {"0001_1화.zip": "a", "info.xml": "<x/>"}); moved, prog, conf, fail = run(target("local", "원본11", "local", "대상11"), keep_last=False)
assert moved == 1 and fail == [] and names(A / "대상11") == ["0001_1화.zip", "info.xml"]
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
mk("원본11b", {"0001_1화.zip": "a"}); moved, prog, conf, fail = run(target("local", "원본11b", "local", "대상11b")); assert moved == 0 and not (A / "대상11b").exists() and names(A / "원본11b") == ["0001_1화.zip"]   # 마지막 1개는 보존, 정보 파일도 없음
print("F11) 표지 예외/빈 원본 OK")

# 12. 정보 파일: 목적지에 커버가 이미 있으면 건너뜀(주기 이동), 복사가 실패해도 이동은 계속
mk("원본12", {"0001_1화.zip": "a", "0002_2화.zip": "b", "info.xml": "<new/>", "cover.jpg": "NEW"}); mk("대상12", {"cover.jpg": "KEEP"})
moved, prog, conf, fail = run(target("local", "원본12", "local", "대상12")); assert moved == 1 and (A / "대상12" / "cover.jpg").read_bytes() == b"KEEP" and (A / "대상12" / "info.xml").read_text() == "<new/>"
orig_copy = archiver._generic_copy_file
def bad_copy(*a, **k): raise OSError("복사 오류")
archiver._generic_copy_file = bad_copy; mk("원본12b", {"0001_1화.zip": "a", "0002_2화.zip": "b", "info.xml": "<x/>"})
moved, prog, conf, fail = run(target("local", "원본12b", "local", "대상12b")); archiver._generic_copy_file = orig_copy
assert moved == 1 and fail == [] and names(A / "대상12b") == ["0001_1화.zip"]
print("F12) 정보 파일 처리 OK")

# 13. 원격 원본 + 로컬 목적지 완결 처리: 새 카카오 표지를 로컬 목적지 cover.jpg로 복사하고 임시 파일 정리 / 올리기가 실패해도 계속하고 임시 파일 정리
def remote_kakao(name):
    fake.files.clear()
    for n, data in (("0001_1화.zip", b"a"), ("info.xml", b'<ComicInfo><Web>https://page.kakao.com/content/888</Web></ComicInfo>')): fake.files[("r", f"{name}/{n}")] = data
tmp1 = Path(tempfile.mkdtemp()) / "c.jpg"; tmp1.write_bytes(b"NEW1"); archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp1)
remote_kakao("카카오13"); moved, prog, conf, fail = run(target("rclone", "r:카카오13", "local", "대상13"), keep_last=False)
assert (A / "대상13" / "cover.jpg").read_bytes() == b"NEW1" and not tmp1.exists() and "[카카오13] 카카오 표지 갱신함" in prog
tmp2 = Path(tempfile.mkdtemp()) / "c.jpg"; tmp2.write_bytes(b"NEW2"); archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp2)
remote_kakao("카카오13b"); orig_copyto = rclone_client.copyto
def bad_copyto(cfg, src, dest):
    if str(src).endswith("c.jpg"): raise rclone_client.RcloneError("업로드 실패")
    return orig_copyto(cfg, src, dest)
rclone_client.copyto = bad_copyto; moved, prog, conf, fail = run(target("rclone", "r:카카오13b", "rclone", "r:대상/13b"), keep_last=False); rclone_client.copyto = orig_copyto
assert moved == 1 and fail == [] and not tmp2.exists() and ("r", "대상/13b/cover.jpg") not in fake.files
print("F13) 원격 원본 표지 복사/실패 OK")

# ═══ bulk_move_folder ═══
def bulk(src_type, src, dest_type, dest, **kw):
    prog = []
    moved = archiver.bulk_move_folder(st.archive_root, st.archive_root, CFG, src_type, src, dest_type, dest, prog.append, kw.get("template", ""), kw.get("regen", False))
    return moved, prog
archiver.get_conflict_policy = lambda: POLICY["v"]; POLICY = {"v": "skip"}

# B1. 로컬→로컬: 폴더 구조 유지, 파일 단위 이력(제목 "원본 → 대상", title_id "-"), 진행 메시지, 빈 폴더 정리
src = mk("일괄원본", {"작품X/0001 1화.zip": "a", "작품X/info.xml": "<x/>", "loose.txt": "t"})
moved, prog = bulk("local", "일괄원본", "local", "일괄대상")
assert moved == 3 and names(A / "일괄대상") == ["loose.txt", "작품X/0001 1화.zip", "작품X/info.xml"] and not src.exists()
assert prog[0] == "이동할 파일 3개 확인, 시작합니다" and prog[-1] == "원본 쪽 빈 폴더 정리 중" and sum("이동 완료" in p for p in prog) == 3 and any(p.startswith("[3/3] 이동 완료") for p in prog)
rows = [r for r in repository.list_archive_history(1, 500)[0] if r["title_id"] == "-"]
assert len(rows) == 3 and {r["title_name"] for r in rows} == {"일괄원본 → 일괄대상"} and sorted(r["file_name"] for r in rows) == ["loose.txt", "작품X/0001 1화.zip", "작품X/info.xml"]
print("B1) 로컬→로컬 일괄 OK")

# B2. 충돌 정책
for policy, expect, moved_n in (("skip", ["a.zip", "b.zip"], 1), ("rename", ["a (2).zip", "a.zip", "b.zip"], 2), ("overwrite", ["a.zip", "b.zip"], 2)):
    POLICY["v"] = policy; mk("일괄원본2", {"a.zip": "new", "b.zip": "B"}); mk("일괄대상2", {"a.zip": "OLD"})
    moved, prog = bulk("local", "일괄원본2", "local", "일괄대상2"); assert moved == moved_n and names(A / "일괄대상2") == expect, (policy, names(A / "일괄대상2"))
    if policy == "skip": assert any(p.startswith("[1/2] 건너뜀(이미 존재)") or p.startswith("[2/2] 건너뜀(이미 존재)") for p in prog) and (A / "일괄대상2" / "a.zip").read_bytes() == b"OLD"
POLICY["v"] = "skip"
print("B2) 일괄 충돌 정책 OK")

# B3. 파일명 템플릿: 하위 폴더 안 파일은 그 폴더 이름이 {title}, 인식 못 하는 파일은 원래 이름
mk("일괄원본3", {"작품Y/0001 작품Y 1화.zip": "a", "작품Y/메모.txt": "m"})
moved, prog = bulk("local", "일괄원본3", "local", "일괄대상3", template="{title}_{episode_no}_{subtitle}")
assert names(A / "일괄대상3") == ["작품Y/메모.txt", "작품Y/작품Y_0001_1화.zip"], names(A / "일괄대상3")
print("B3) 일괄 템플릿 OK")

# B4. 설정 오류는 예외
for args, msg in ((("local", "없는원본", "local", "x"), "원본 폴더가 존재하지 않습니다."), (("rclone", ":경로", "local", "x"), "원본 원격 정보가 올바르지 않습니다.")):
    try: bulk(*args); raise AssertionError("예외가 나야 함")
    except ValueError as e: assert str(e) == msg, str(e)
mk("일괄원본4", {"a.zip": "a"})
try: bulk("local", "일괄원본4", "rclone", ":경로"); raise AssertionError("예외가 나야 함")
except ValueError as e: assert str(e) == "목적지 원격 정보가 올바르지 않습니다."
print("B4) 일괄 설정 오류 OK")

# B5. 로컬→원격 / 원격→로컬 / 원격→원격
fake.files.clear(); mk("일괄원본5", {"a/1.zip": "1", "b.txt": "b"})
moved, prog = bulk("local", "일괄원본5", "rclone", "r:백업/5"); assert moved == 2 and remote("백업/5") == ["백업/5/a/1.zip", "백업/5/b.txt"] and names(A / "일괄원본5") == []
moved, prog = bulk("rclone", "r:백업/5", "local", "일괄대상5"); assert moved == 2 and names(A / "일괄대상5") == ["a/1.zip", "b.txt"] and remote("백업/5") == []
fake.files[("r", "원본R/x/2.zip")] = b"2"; moved, prog = bulk("rclone", "r:원본R", "rclone", "r:대상R"); assert moved == 1 and remote("") == ["대상R/x/2.zip"]
print("B5) 네 조합 OK")

# B6. 카카오 표지 갱신 옵션: 로컬 원본 / 원격 원본(임시 표지로 cover를 교체해서 옮김)
mk("일괄원본6", {"info.xml": "<x/>", "0001_1화.zip": "a"}); kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: True
moved, prog = bulk("local", "일괄원본6", "local", "일괄대상6", regen=True); assert prog[0] == "카카오 표지 갱신함" and moved == 2
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
fake.files.clear()
for n, data in (("info.xml", b'<ComicInfo><Web>https://page.kakao.com/content/777</Web></ComicInfo>'), ("cover.png", b"OLD"), ("0001_1화.zip", b"z")): fake.files[("r", f"카카오R/{n}")] = data
tmp_cover = Path(tempfile.mkdtemp()) / "c.jpg"; tmp_cover.write_bytes(b"NEW"); archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp_cover) if sid == "777" else None
moved, prog = bulk("rclone", "r:카카오R", "rclone", "r:보관/카카오R", regen=True)
assert moved == 3 and fake.files[("r", "보관/카카오R/cover.jpg")] == b"NEW" and ("r", "보관/카카오R/cover.png") not in fake.files and remote("카카오R") == [] and not tmp_cover.exists()
assert prog[0] == "카카오 표지 갱신함" and any("이동 완료(표지 갱신): cover.jpg" in p for p in prog)
print("B6) 일괄 표지 갱신 OK")

# B7. 파일 하나가 실패해도 계속
mk("일괄원본7", {"a.zip": "a", "b.zip": "b", "c.zip": "c"})
def flaky2(**kw):
    if kw["rel_path"] == "b.zip": raise OSError("권한 없음")
    return orig_single(**kw)
archiver._bulk_move_single_file = flaky2; moved, prog = bulk("local", "일괄원본7", "local", "일괄대상7"); archiver._bulk_move_single_file = orig_single
assert moved == 2 and any("실패(건너뜀): b.zip — 권한 없음" in p for p in prog) and names(A / "일괄원본7") == ["b.zip"] and names(A / "일괄대상7") == ["a.zip", "c.zip"]
print("B7) 일괄 실패 격리 OK")

# B8. 카카오 표지 갱신 중 예외는 무시하고 이동은 계속
mk("일괄원본8", {"info.xml": "<x/>", "0001_1화.zip": "a"}); kakao_cover.refresh_kakao_cover_if_applicable = boom_refresh
moved, prog = bulk("local", "일괄원본8", "local", "일괄대상8", regen=True); kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
assert moved == 2 and "카카오 표지 갱신함" not in prog
# B9. 원격 원본에 cover 파일이 없으면 받아 둔 임시 표지는 쓰이지 않고 정리된다 / 원격→로컬에서 표지를 교체해서 옮김
fake.files.clear()
for n, data in (("info.xml", b'<ComicInfo><Web>https://page.kakao.com/content/999</Web></ComicInfo>'), ("0001_1화.zip", b"z")): fake.files[("r", f"카카오S/{n}")] = data
tmp3 = Path(tempfile.mkdtemp()) / "c.jpg"; tmp3.write_bytes(b"NEW3"); archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp3)
moved, prog = bulk("rclone", "r:카카오S", "local", "일괄대상9", regen=True); assert moved == 2 and names(A / "일괄대상9") == ["0001_1화.zip", "info.xml"] and not tmp3.exists()
fake.files.clear()
for n, data in (("info.xml", b'<ComicInfo><Web>https://page.kakao.com/content/999</Web></ComicInfo>'), ("cover.png", b"OLD")): fake.files[("r", f"카카오T/{n}")] = data
tmp4 = Path(tempfile.mkdtemp()) / "c.jpg"; tmp4.write_bytes(b"NEW4"); archiver._download_kakao_cover_to_temp_file = lambda sid: str(tmp4)
moved, prog = bulk("rclone", "r:카카오T", "local", "일괄대상9b", regen=True); assert (A / "일괄대상9b" / "cover.jpg").read_bytes() == b"NEW4" and names(A / "일괄대상9b") == ["cover.jpg", "info.xml"] and not tmp4.exists()
print("B8~B9) 일괄 표지 예외/임시 파일 OK")
print("\n전부 통과")
