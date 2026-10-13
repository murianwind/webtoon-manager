"""회차 폴더 압축(zipper.py) 특성화 테스트 — "[번호] 부제목" 폴더를 zip으로 묶고 원본 폴더를 지우는 현재 동작과 번호 이어붙이기 규칙을 고정한다."""
import tempfile
import zipfile
from pathlib import Path

from app import zipper


def make_episode(parent, name, files):
    folder = parent / name
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return folder


# Scenario 1: 이름 정리 — 대괄호는 지우고 전각 콜론은 공백으로, 겹친 공백은 하나로
assert zipper._clean_name("[a]：b  c") == "a b c" and zipper._clean_name("그냥 이름") == "그냥 이름"

# Scenario 2: 폴더가 아니거나 없으면 빈 목록(아무것도 안 한다)
assert zipper.zip_episode_folders(Path(tempfile.mkdtemp()) / "없는 폴더") == []
plain_file = Path(tempfile.mkdtemp()) / "파일.txt"; plain_file.write_text("x")
assert zipper.zip_episode_folders(plain_file) == []

# Scenario 3: 회차 폴더 하나 — "{번호 3자리} {작품 폴더 이름} {부제목}.zip", 안의 파일은 회차 폴더 기준 상대 경로, 원본 폴더는 지운다
root = Path(tempfile.mkdtemp()); title_dir = root / "작품： 이름"; title_dir.mkdir()                         # 다운로드가 만드는 폴더 이름은 콜론이 전각이다(remove_forbidden_str)
make_episode(title_dir, "[0001] 첫 화", {"0001.jpg": b"a", "0002.jpg": b"b", "하위/0003.png": b"c"})
created = zipper.zip_episode_folders(title_dir)
assert created == ["001 작품 이름 첫 화.zip"], created
with zipfile.ZipFile(title_dir / created[0]) as archive:
    assert sorted(archive.namelist()) == ["0001.jpg", "0002.jpg", "하위/0003.png"] and archive.read("0002.jpg") == b"b"
assert not (title_dir / "[0001] 첫 화").exists()

# Scenario 4: 번호 이어붙이기 — 이미 있는 zip의 마지막 번호 + 1이 아니면 끊기지 않게 마지막 번호 + 1로 붙인다(네이버 회차 번호가 건너뛰어도 일련번호는 이어진다)
root = Path(tempfile.mkdtemp()); title_dir = root / "작품"; title_dir.mkdir()
(title_dir / "005 작품 예전 회차.zip").write_bytes(b"x")
(title_dir / "목록.txt").write_text("zip이 아닌 파일은 번호 계산에서 뺀다")
(title_dir / "이름이숫자아님.zip").write_bytes(b"x")                                   # 앞자리가 숫자가 아닌 zip도 뺀다
make_episode(title_dir, "[0003] 세 번째", {"0001.jpg": b"a"})                       # 5 다음은 6이어야 한다(3이 아니라)
assert zipper.zip_episode_folders(title_dir) == ["006 작품 세 번째.zip"]
make_episode(title_dir, "[0007] 일곱 번째", {"0001.jpg": b"a"})                     # 마지막이 6이고 폴더 번호가 7이면 그대로 7
assert zipper.zip_episode_folders(title_dir) == ["007 작품 일곱 번째.zip"]
assert zipper._next_zip_number(Path(tempfile.mkdtemp()), 9) == 9                       # zip이 하나도 없으면 폴더 번호 그대로

# Scenario 4-2: 부제목이 없는 "[0004]"만 있는 폴더 이름은 폴더 이름 전체를 부제목 자리에 쓴다
root = Path(tempfile.mkdtemp()); title_dir = root / "작품"; title_dir.mkdir()
make_episode(title_dir, "[0004]", {"0001.jpg": b"a"})
assert zipper.zip_episode_folders(title_dir) == ["004 작품 0004.zip"]

# Scenario 5: 대괄호 번호가 없는 폴더는 건드리지 않는다
root = Path(tempfile.mkdtemp()); title_dir = root / "작품"; title_dir.mkdir()
make_episode(title_dir, "기타 자료", {"a.txt": b"x"})
assert zipper.zip_episode_folders(title_dir) == [] and (title_dir / "기타 자료" / "a.txt").exists()

# Scenario 6: 폴더가 여럿이면 모두 압축하고 원본을 지우며, 일련번호는 겹치지 않는다(순서는 파일시스템이 정하므로 번호는 비교하지 않는다)
root = Path(tempfile.mkdtemp()); title_dir = root / "작품"; title_dir.mkdir()
for number in (1, 2, 3): make_episode(title_dir, f"[{number:04d}] {number}화", {"0001.jpg": b"x"})
created = zipper.zip_episode_folders(title_dir)
assert len(created) == 3 and len({name.split(" ")[0] for name in created}) == 3
assert sorted(p.name for p in title_dir.iterdir()) == sorted(created)

# Scenario 7: 압축 중 오류가 나면 그 회차는 건너뛰고(폴더는 그대로 둠) 계속한다 — 반쪽 결과로 원본을 지우지 않는다
root = Path(tempfile.mkdtemp()); title_dir = root / "작품"; title_dir.mkdir()
make_episode(title_dir, "[0001] 실패할 화", {"0001.jpg": b"x"})
real_zip = zipper.zipfile.ZipFile
def broken(*args, **kwargs): raise OSError("디스크 가득 참")
zipper.zipfile.ZipFile = broken
try:
    assert zipper.zip_episode_folders(title_dir) == []
finally:
    zipper.zipfile.ZipFile = real_zip
assert (title_dir / "[0001] 실패할 화" / "0001.jpg").exists()
print("ALL OK")
