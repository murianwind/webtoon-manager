"""테스트 위생 검사 — 내 컴퓨터/샌드박스에서만 되는 경로나, 시스템 루트에 폴더를 만드는 설정이 테스트에 들어가지 않게 한다.

배경: 테스트를 루트 권한으로 돌리면 `/dl` 같은 시스템 루트 폴더를 만들 수 있어서 통과하지만, 깃헙 액션(일반 사용자)에서는 PermissionError로
실패한다(실제로 그랬다). 작업 경로를 박아 둔 서브프로세스도 러너의 다른 경로에서는 깨진다. 이런 걸 소스 단계에서 막는다.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "tests").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.js"))
FILES = [f for f in FILES if "node_modules" not in f.parts and f.name != "test_hygiene.py"]   # 이 파일은 금지 문자열을 검사 규칙으로 적어 둔다
assert len(FILES) > 20, FILES

FORBIDDEN_LITERALS = ("/home/claude", "/mnt/user-data", "/tmp/")                           # 샌드박스/개발 환경 전용 경로 — 임시 폴더는 tempfile로
ROOT_DIR_SETTING = re.compile(r"""(download_root|archive_root|database_path|cookie_file_path)\s*=\s*["']/[^"']+["']""")   # 시스템 루트 아래 경로를 설정으로 박음
problems = []
for f in FILES:
    for number, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
        for literal in FORBIDDEN_LITERALS:
            if literal in line:
                problems.append(f"{f.relative_to(ROOT)}:{number} 금지 경로 {literal!r}: {line.strip()[:90]}")
        if ROOT_DIR_SETTING.search(line):
            problems.append(f"{f.relative_to(ROOT)}:{number} 시스템 루트 아래 경로를 설정으로 사용(임시 폴더를 쓰세요): {line.strip()[:90]}")
assert not problems, "\n" + "\n".join(problems)
print(f"테스트 위생 OK ({len(FILES)}개 파일 검사)")
print("\n전부 통과")
