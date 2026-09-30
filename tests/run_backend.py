"""백엔드 테스트 실행기 — tests/backend의 스크립트를 하나씩, 매번 새 임시 폴더(DB/다운로드/보관/쿠키)로 돌린다.

사용법:  python tests/run_backend.py            # 전부
         python tests/run_backend.py resync     # 이름에 resync가 들어간 것만
         python tests/run_backend.py --coverage # 실행한 줄을 .coverage.*에 기록(이어서: python -m coverage combine && python -m coverage report)
각 테스트는 독립된 프로세스로 실행되고(전역 상태가 섞이지 않게), 실패하면 마지막 출력을 보여준 뒤 종료 코드 1로 끝난다.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = sorted((ROOT / "tests" / "backend").glob("test_*.py"))
COVERAGE = "--coverage" in sys.argv


def run_one(script: Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for name in ("data", "archive", "download"):
            (base / name).mkdir()
        env = {
            **os.environ,
            "DATABASE_PATH": str(base / "data" / "t.db"), "ARCHIVE_ROOT": str(base / "archive"),
            "DOWNLOAD_ROOT": str(base / "download"), "COOKIE_FILE_PATH": str(base / "cookies.json"),
            "PYTHONPATH": str(ROOT), "PYTHONWARNINGS": "ignore",
        }
        cmd = [sys.executable, str(script)]
        if COVERAGE:  # --coverage: 실행한 줄을 기록한다(테스트가 어느 코드를 안 거치는지 볼 때)
            cmd = [sys.executable, "-m", "coverage", "run", "--parallel-mode", f"--data-file={ROOT / '.coverage'}", f"--source={ROOT / 'app'}", str(script)]
        result = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        return result.returncode == 0, (result.stdout + result.stderr)[-1500:]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pattern = args[0] if args else ""
    selected = [t for t in TESTS if pattern in t.name]
    failed = 0
    for script in selected:
        ok, output = run_one(script)
        print(f"{'PASS' if ok else 'FAIL'}  {script.stem}")
        if not ok:
            failed += 1
            print(output)
    print(f"\n{len(selected) - failed}/{len(selected)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
