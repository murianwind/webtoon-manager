"""아카이빙(정기와 수동 실행): 웹툰 뷰어에서 읽지 않은 회차는 건너뛴다 — 필터(순수 함수), _archive_title, run_periodic_archive/manual_archive_now(조회 함수 주입).
뷰어가 설정돼 있는데 읽음 상태를 확인하지 못하면(연결 실패, 작품을 못 찾음 등) 그 작품은 **옮기지 않는다**(예전에는 "알 수 없음"으로 보고 전부 옮겨서, 뷰어가 잠깐 불안정한 날 안 읽은 회차까지 이동했다).
완결 처리 이동(full_move)과 폴더 대상은 읽음 상태를 보지 않는다. 뷰어가 설정되지 않았으면(조회 함수 없음) 예전처럼 마지막 회차만 남기고 옮긴다."""
from pathlib import Path

from app import db, repository, archiver
from app.archiver.params import ProgressReport, TitleArchiveJob
from app.archiver.read_filter import filter_read_files
from app.config import get_settings

db.get_connection()
st = get_settings()
A = Path(st.archive_root); D = Path(st.download_root); A.mkdir(parents=True, exist_ok=True); D.mkdir(parents=True, exist_ok=True)


def make(title, nums):
    d = D / title
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*.zip"): p.unlink()
    for n in nums: (d / f"{n:04d} {n}화.zip").write_bytes(f"zip{n}".encode())
    return d
def left(d): return sorted(p.name for p in Path(d).glob("*.zip"))
def job(title, base, read_numbers=None, keep_last=True, trigger="periodic"):
    return TitleArchiveJob(archive_root=st.archive_root, download_root=st.download_root, title_id="88", title_name=title, base_path=base, policy="skip",
                           trigger_type=trigger, keep_last=keep_last, read_numbers=read_numbers)


# ── 1. 필터: Given 회차 파일 목록 / When 읽은 번호로 거르면 / Then 읽은 회차만 남고, 조회 실패(None)면 그대로 ──
files = [(1, Path("a")), (2, Path("b")), (3, Path("c"))]
assert filter_read_files(files, frozenset({1, 3})) == [(1, Path("a")), (3, Path("c"))]
assert filter_read_files(files, frozenset()) == []                                    # 하나도 안 읽었으면 아무것도 안 옮긴다
assert filter_read_files(files, None) == files                                         # 필터를 안 쓰는 경우(뷰어 미설정/완결 이동) → 전부 통과 — 조회 실패는 호출부(run_periodic_archive/manual_archive_now)가 그 작품을 건너뛴다
print("1) 필터 OK (읽은 회차만, 빈 집합은 전부 건너뜀, None은 필터 없음)")

# ── 2. _archive_title: Given 1~4화, 1·2화만 읽음 / When 정기 아카이빙 / Then 1·2화만 이동, 3화(안 읽음)와 4화(마지막)는 남음 ──
src = make("작품A", [1, 2, 3, 4])
moved = archiver._archive_title(job("작품A", "보관A", read_numbers=frozenset({1, 2})), ProgressReport())
assert moved == 2 and left(src) == ["0003 3화.zip", "0004 4화.zip"] and left(A / "보관A") == ["0001 1화.zip", "0002 2화.zip"], (left(src), left(A / "보관A"))
print("2) 읽은 회차만 이동 OK")

# ── 3. 마지막 회차 보존과 조합: 4화까지 전부 읽어도 마지막(4화)은 남는다 ──
src = make("작품B", [1, 2, 3, 4])
moved = archiver._archive_title(job("작품B", "보관B", read_numbers=frozenset({1, 2, 3, 4})), ProgressReport())
assert moved == 3 and left(src) == ["0004 4화.zip"], left(src)
print("3) 마지막 회차 보존과 조합 OK")

# ── 4. 필터를 안 쓰는 경우(read_numbers=None: 뷰어 미설정): 마지막만 남기고 전부 이동 ──
src = make("작품C", [1, 2, 3])
moved = archiver._archive_title(job("작품C", "보관C", read_numbers=None), ProgressReport())
assert moved == 2 and left(src) == ["0003 3화.zip"]
print("4) 필터 없음(뷰어 미설정) 시 기존 동작 OK")

# ── 5. 전체 이동(완결)은 read_numbers를 줘도 호출부가 넘기지 않으므로 기존대로 전부 — 필터는 job에 값이 있을 때만 작동 ──
src = make("작품D", [1, 2, 3])
moved = archiver._archive_title(job("작품D", "보관D", keep_last=False, trigger="manual_finish"), ProgressReport())
assert moved == 3 and left(src) == []
print("5) 전체 이동은 필터 없음 OK")

# ── 6. run_periodic_archive: 조회 함수를 주입하면 제목별로 호출해 적용 — 읽음 조회가 실패(None)한 작품은 옮기지 않고 실패 목록에 남긴다. 주입 안 하면(뷰어 미설정) 기존 동작 ──
repository.upsert_new(title_id="901", title="정기E", added_source=repository.SOURCE_MANUAL)
repository.upsert_new(title_id="902", title="정기F", added_source=repository.SOURCE_MANUAL)
for tid in ("901", "902"): repository.upsert_archive_target(tid, f"보관{tid}", enabled=True)
srcE = make("정기E", [1, 2, 3]); srcF = make("정기F", [1, 2, 3])
asked = []
def provider(title_name, kakao):
    asked.append((title_name, kakao))
    return frozenset({1}) if title_name == "정기E" else None                         # F는 조회 실패
failures = []
total = archiver.run_periodic_archive(st.archive_root, st.download_root, failure_log=failures, read_numbers_provider=provider)
assert sorted(asked) == [("정기E", False), ("정기F", False)], asked
assert left(srcE) == ["0002 2화.zip", "0003 3화.zip"] and left(srcF) == ["0001 1화.zip", "0002 2화.zip", "0003 3화.zip"] and total == 1, (left(srcE), left(srcF), total)    # F는 조회 실패 → 하나도 안 옮김
assert [(t, f) for t, f, _e in failures] == [("정기F", "(읽음 상태 확인 실패)")] and "읽음 상태" in failures[0][2], failures                         # 디스코드 알림에 쓰이는 실패 목록
srcE = make("정기E", [11, 12, 13])                                                         # 앞에서 보관한 회차와 이름이 겹치면 충돌 정책이 작동하므로 새 회차로
archiver.run_periodic_archive(st.archive_root, st.download_root)                          # 주입 안 함(뷰어 미설정) → 기존 동작
assert left(srcE) == ["0013 13화.zip"]
print("6) run_periodic_archive 조회 함수 주입 OK (조회 실패는 건너뜀)")

# ── 7. 수동 실행도 읽은 회차만 옮긴다(완결 처리가 아닌 일반 수동 이동) — 안 읽은 회차와 마지막 회차는 남는다 ──
srcE = make("정기E", [21, 22, 23, 24])
manual_msgs = []
archiver.manual_archive_now(st.archive_root, st.download_root, ["901"], progress_callback=manual_msgs.append, read_numbers_provider=lambda t, k: frozenset({21, 22}))
assert left(srcE) == ["0023 23화.zip", "0024 24화.zip"], left(srcE)
assert any("읽지 않은 1개 회차는 건너뜀" in m for m in manual_msgs), manual_msgs
print("7) 수동 실행도 읽은 회차만 OK")

# ── 8. 수동 실행도 뷰어가 설정되지 않았으면(조회 함수 없음) 예전처럼 마지막만 남기고 옮긴다 ──
srcE = make("정기E", [31, 32, 33])
archiver.manual_archive_now(st.archive_root, st.download_root, ["901"])
assert left(srcE) == ["0033 33화.zip"], left(srcE)
print("8) 수동 실행(뷰어 미설정) 기존 동작 OK")

# ── 9. 수동 실행에서 읽음 조회가 실패(None)하면 그 작품은 옮기지 않고 알린다 ──
srcE = make("정기E", [41, 42, 43])
manual_msgs = []
moved = archiver.manual_archive_now(st.archive_root, st.download_root, ["901"], progress_callback=manual_msgs.append, read_numbers_provider=lambda t, k: None)
assert moved == 0 and left(srcE) == ["0041 41화.zip", "0042 42화.zip", "0043 43화.zip"], left(srcE)
assert any("읽음 상태를 확인하지 못해" in m and "정기E" in m for m in manual_msgs), manual_msgs
print("9) 수동 실행 조회 실패는 건너뜀 OK")

# ── 10. 완결 처리 이동(full_move)은 읽음 상태를 보지 않는다 — 조회 함수가 있어도 부르지 않고 마지막 회차까지 전부 옮긴다 ──
srcE = make("정기E", [51, 52, 53]); called = []
def never(title_name, kakao): called.append(title_name); return frozenset()
moved = archiver.manual_archive_now(st.archive_root, st.download_root, ["901"], full_move=True, read_numbers_provider=never)
assert not called and moved == 3 and left(srcE) == [], (called, moved, left(srcE))
print("10) 완결 처리 이동은 읽음 상태 안 봄 OK")

print("\n전부 통과")
