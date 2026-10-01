"""archiver._archive_title 특성 테스트 — 로컬/rclone 목적지, 마지막 회차 보존/완결 전체 이동, 충돌 정책, 실패 격리, 메타데이터, 파일명 템플릿, 설정 누락."""
import tempfile
from pathlib import Path

from app import db, repository, archiver, kakao_cover
from app.config import get_settings
from fake_rclone import FakeRclone   # tests/backend 안의 도우미

db.get_connection()
st = get_settings()
A = Path(st.archive_root); D = Path(st.download_root); A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)
fake = FakeRclone().install()
cfg = Path(tempfile.mkdtemp()) / "rclone.conf"; cfg.write_text("[r]\n")

def make(title, nums, info="<ComicInfo>v1</ComicInfo>", cover=b"COVER"):
    d = D / title
    if d.exists():
        for p in d.iterdir(): p.unlink()
    d.mkdir(parents=True, exist_ok=True)
    for n in nums: (d / f"{n:04d} {n}화.zip").write_bytes(f"zip{n}".encode())
    if info: (d / "info.xml").write_text(info, encoding="utf-8")
    if cover: (d / "cover.jpg").write_bytes(cover)
    return d
def call(title, base_path, keep_last=True, policy="skip", dest_type="local", **kw):
    prog, conf, fail = [], [], []
    moved = archiver._archive_title(st.archive_root, st.download_root, "77", title, base_path, policy, "manual" if keep_last else "manual_finish", keep_last,
                                    dest_type=dest_type, rclone_config_path=str(cfg) if dest_type == "rclone" else "", progress_callback=prog.append,
                                    conflict_log=conf, failure_log=fail, writer_names=kw.get("writers", ["글쓴이"]), **{k: v for k, v in kw.items() if k != "writers"})
    return moved, prog, conf, fail
def hist(): return sorted(r["file_name"] for r in repository.list_archive_history(1, 200)[0] if r["title_id"] == "77")
def names(d): return sorted(p.name for p in Path(d).iterdir())

# ── 1. 로컬: 마지막 회차는 남기고 나머지를 옮김(+이력+진행 메시지), 정보 파일은 복사(항상 덮어쓰기)/커버는 없을 때만 ──
src = make("작품A", [1, 2, 3])
moved, prog, conf, fail = call("작품A", "보관A")
dest = A / "보관A"
assert moved == 2 and names(src) == ["0003 3화.zip", "cover.jpg", "info.xml"] and names(dest) == ["0001 1화.zip", "0002 2화.zip", "cover.jpg", "info.xml"], (names(src), names(dest))
assert hist() == ["0001 1화.zip", "0002 2화.zip"] and "[작품A] 이동 완료: 0001 1화.zip" in prog and conf == [] and fail == []
(src / "info.xml").write_text("<ComicInfo>v2</ComicInfo>"); (src / "cover.jpg").write_bytes(b"NEWCOVER"); (src / "0004 4화.zip").write_bytes(b"zip4")
call("작품A", "보관A")
assert (dest / "info.xml").read_text() == "<ComicInfo>v2</ComicInfo>" and (dest / "cover.jpg").read_bytes() == b"COVER"            # info.xml은 덮어쓰고 커버는 이미 있으면 건너뜀
print("1) 로컬 기본 이동 OK")

# ── 2. 충돌 정책 ──
for policy, expect_names, expect_moved, expect_conf in (("skip", ["0001 1화.zip"], 0, 1), ("overwrite", ["0001 1화.zip"], 1, 1), ("rename", ["0001 1화 (2).zip", "0001 1화.zip"], 1, 1)):
    make("작품B", [1, 2], info=None, cover=None); (A / "보관B").mkdir(exist_ok=True)
    for p in (A / "보관B").iterdir(): p.unlink()
    (A / "보관B" / "0001 1화.zip").write_bytes(b"OLD")
    moved, prog, conf, fail = call("작품B", "보관B", policy=policy)
    got = sorted(p.name for p in (A / "보관B").glob("0001*.zip"))
    assert moved == expect_moved and got == expect_names and len(conf) == expect_conf and conf[0][:2] == ("작품B", "0001 1화.zip"), (policy, moved, got, conf)
    if policy == "skip": assert "[작품B] 건너뜀(이미 존재): 0001 1화.zip" in prog and (A / "보관B" / "0001 1화.zip").read_bytes() == b"OLD"
    if policy == "overwrite": assert (A / "보관B" / "0001 1화.zip").read_bytes() == b"zip1"
print("2) 충돌 정책 OK (skip/overwrite/rename, 충돌 기록)")

# ── 3. 완결 전체 이동: 마지막 회차까지 옮기고 정보 파일/커버도 옮긴다(원본에서 사라짐), 카카오 표지 갱신은 실패해도 계속 ──
src = make("작품C", [1, 2, 3]); refreshed = []
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: refreshed.append(d.name) or True
moved, prog, conf, fail = call("작품C", "보관C", keep_last=False)
assert moved == 3 and refreshed == ["작품C"] and "[작품C] 카카오 표지 갱신함" in prog and names(src) == [] and names(A / "보관C") == ["0001 1화.zip", "0002 2화.zip", "0003 3화.zip", "cover.jpg", "info.xml"]
def boom(d, **k): raise OSError("표지 오류")
kakao_cover.refresh_kakao_cover_if_applicable = boom
src = make("작품C2", [1, 2]); moved, prog, conf, fail = call("작품C2", "보관C2", keep_last=False); assert moved == 2 and fail == []          # 예외가 아카이빙을 막지 않는다
kakao_cover.refresh_kakao_cover_if_applicable = lambda d, **k: False
print("3) 완결 전체 이동 OK")

# ── 4. 한 파일의 이동이 실패해도 나머지는 계속, 실패 기록 ──
src = make("작품D", [1, 2, 3, 4], info=None, cover=None)
orig = archiver._move_episode_file
def flaky(dest_type, s, name, policy, *a):
    if s.name.startswith("0002"): raise OSError("디스크 오류")
    return orig(dest_type, s, name, policy, *a)
archiver._move_episode_file = flaky
moved, prog, conf, fail = call("작품D", "보관D"); archiver._move_episode_file = orig
assert moved == 2 and fail == [("작품D", "0002 2화.zip", "디스크 오류")] and "[작품D] 이동 실패: 0002 2화.zip — 디스크 오류" in prog and names(A / "보관D") == ["0001 1화.zip", "0003 3화.zip"]
print("4) 실패 격리 OK")

# ── 5. 파일명 템플릿 ──
src = make("작품E", [], info=None, cover=None)
for n in (1, 2, 3): (src / f"{n:04d} 작품E {n}화.zip").write_bytes(b"z")                                        # 실제 네이버 zip 이름: {번호} {제목} {부제목}
archiver._get_effective_filename_template = lambda preset_id: "{title} {episode_no}화 - {author}"
call("작품E", "보관E"); names_e = names(A / "보관E")
assert names_e == ["작품E 0001화 - 글쓴이.zip", "작품E 0002화 - 글쓴이.zip"], names_e          # {title} {episode_no}(원래 자릿수 유지) {author}(글 작가)
archiver._get_effective_filename_template = lambda preset_id: ""
print("5) 파일명 템플릿 OK")

# ── 6. 설정이 없으면 아무것도 안 한다 ──
src = make("작품F", [1, 2])
moved = archiver._archive_title("", st.download_root, "77", "작품F", "보관F", "skip", "manual", True)                       # ARCHIVE_ROOT 없음
assert moved == 0 and len(names(src)) == 4                                                      # zip 2개 + info.xml + cover.jpg 그대로
moved = archiver._archive_title(st.archive_root, st.download_root, "77", "작품F", "r:보관", "skip", "manual", True, dest_type="rclone", rclone_config_path="/없는/설정")   # rclone 설정 파일 없음
assert moved == 0 and len(names(src)) == 4                                                      # zip 2개 + info.xml + cover.jpg 그대로
assert archiver._archive_title(st.archive_root, st.download_root, "77", "없는작품", "보관", "skip", "manual", True) == 0                    # 폴더가 없으면 0
print("6) 설정 누락/폴더 없음 OK")

# ── 7. rclone 목적지: 원격 폴더로 올리고(원본 삭제), 정보 파일은 복사/이동 ──
default_base = "r:웹툰"; repository.set_setting(archiver._DEFAULT_BASE_PATH_SETTING_KEY, default_base)                              # 기본 경로면 작품별 서브폴더
src = make("작품G", [1, 2, 3])
moved, prog, conf, fail = call("작품G", default_base, dest_type="rclone")
remote_files = sorted(p for (r, p) in fake.files if r == "r")
assert moved == 2 and names(src) == ["0003 3화.zip", "cover.jpg", "info.xml"] and remote_files == ["웹툰/작품G/0001 1화.zip", "웹툰/작품G/0002 2화.zip", "웹툰/작품G/cover.jpg", "웹툰/작품G/info.xml"], remote_files
assert fake.files[("r", "웹툰/작품G/0001 1화.zip")] == b"zip1" and "[작품G] 이동 완료: 0001 1화.zip" in prog
fake.files.clear(); src = make("작품H", [1, 2])
fake.files[("r", "웹툰/작품H/0001 1화.zip")] = b"OLD"
moved, prog, conf, fail = call("작품H", default_base, dest_type="rclone", policy="skip")
assert moved == 0 and conf and conf[0][:2] == ("작품H", "0001 1화.zip") and "[작품H] 건너뜀(이미 존재): 0001 1화.zip" in prog      # 원격에 이미 있으면 정책 적용
assert fake.files[("r", "웹툰/작품H/0001 1화.zip")] == b"OLD"
fake.files.clear(); src = make("작품I", [1, 2], info=None, cover=None); fake.fail_move_names.add("0001 1화.zip")                   # 업로드 실패도 격리
moved, prog, conf, fail = call("작품I", default_base, dest_type="rclone", keep_last=False)
assert moved == 1 and fail and fail[0][0] == "작품I" and "업로드 실패" in fail[0][2] and ("r", "웹툰/작품I/0002 2화.zip") in fake.files
print("7) rclone 목적지 OK")
print("\n전부 통과")
