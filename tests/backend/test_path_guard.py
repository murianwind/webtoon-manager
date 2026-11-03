"""경로 검사 공용 함수(app/path_guard.py) — 루트 폴더 아래의 상대경로를 실제 경로로 바꾸되 루트 밖으로 못 나가게 한다.
폴더 조회/생성 API와 일괄 이동/아카이빙(archiver)이 모두 이 한 곳을 쓴다(예전에는 같은 네 줄이 세 곳에 복사돼 있었다). 모든 호출 경로를 통한 공격 방어는 test_path_escape가 확인한다.
"""
import os
import tempfile
from pathlib import Path

from app.path_guard import PathOutsideRootError, resolve_under_root

tmp = Path(tempfile.mkdtemp()).resolve(); root = tmp / "root"; outside = tmp / "outside"; sibling = tmp / "root_evil"
for d in (root / "a" / "b", outside, sibling): d.mkdir(parents=True)
os.symlink(outside, root / "link")

# Scenario 1: 루트 안은 통과 — 빈 경로/점은 루트 자신, 안에서 오르내려도 결과가 루트 안이면 허용, 아직 없는 경로도 계산한다. 반환은 해석된(resolve) 절대경로
assert resolve_under_root(str(root), "") == root and resolve_under_root(str(root), ".") == root and resolve_under_root(str(root), "a/b") == root / "a" / "b"
assert resolve_under_root(str(root), "a/../a/b") == root / "a" / "b" and resolve_under_root(str(root), "없는/폴더") == root / "없는" / "폴더"
assert resolve_under_root(str(root) + "/", "a") == root / "a", "루트 끝 슬래시"
assert resolve_under_root(str(tmp / "root" / "a" / ".." ), "a") == root / "a", "루트 자체가 ..을 포함해도 해석한 뒤 비교"
print("1) 루트 안 OK")

# Scenario 2: 밖으로 나가는 경로는 모두 PathOutsideRootError(ValueError의 하위) — 위로 오르기, 이름이 같은 형제 폴더, 절대경로, 심볼릭 링크
for attack in ("..", "../outside", "a/../../outside", f"../{sibling.name}", str(outside), "/etc", "link", "link/x", "a/../../../.."):
    try:
        resolve_under_root(str(root), attack); raise AssertionError(f"막혀야 한다: {attack}")
    except PathOutsideRootError as e:
        assert isinstance(e, ValueError) and "루트 밖" in str(e)
print("2) 루트 밖 차단 OK")
print("ALL OK")
