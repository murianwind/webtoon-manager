"""경로 검사 — 루트 폴더 아래의 상대경로를 실제 경로로 바꾸되 루트 밖으로 못 나가게 한다.

이 앱은 외부에서 열 수 있어서 사용자가 보낸 폴더 경로(../, 절대경로, 심볼릭 링크)가 통하면 서버의 다른 폴더를 읽거나 만들 수 있다. 폴더 조회/생성 API와
일괄 이동/아카이빙이 모두 이 한 곳으로 검사한다(어느 한 곳이라도 빠지면 test_path_escape가 잡는다).
"""
from pathlib import Path


class PathOutsideRootError(ValueError):
    """해석한 경로가 루트 밖이다."""


def resolve_under_root(root: str, relative_path: str) -> Path:
    """root 기준 상대경로를 해석(resolve)한 절대경로로 돌려준다. 루트 자신이거나 루트 아래가 아니면 PathOutsideRootError.
    심볼릭 링크는 따라간 뒤 비교하고, 이름이 루트로 "시작"하는 형제 폴더는 통과시키지 않는다(문자열 접두사가 아니라 부모 목록으로 비교)."""
    resolved_root = Path(root).resolve()
    target = (resolved_root / relative_path).resolve()
    if resolved_root != target and resolved_root not in target.parents:
        raise PathOutsideRootError("경로가 아카이브 루트 밖입니다.")
    return target
