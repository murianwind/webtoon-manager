"""GitHub Actions 러너 이미지는 날짜가 정해진 버전으로 고정한다 — `ubuntu-latest`는 2026-10-19부터 몇 주에 걸쳐 Ubuntu 26.04로 바뀌어서,
배포 파이프라인(테스트 → 이미지 빌드 → 자동 갱신)이 예고 없이 깨질 수 있다. 26.04로 옮길 때는 `ubuntu-26.04`로 먼저 시험해 본 뒤 이 값을 바꾼다."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINNED = "ubuntu-24.04"

runs_on = []
for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
    for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"\s*runs-on:\s*(\S+)", line)
        if match:
            runs_on.append((workflow.name, number, match.group(1)))

# Scenario 1: 작업이 있고(검사가 비어 있지 않고), 하나도 `-latest`를 쓰지 않는다
assert runs_on, "runs-on을 하나도 못 찾았다 — 워크플로 위치나 형식이 바뀌었나?"
assert not [r for r in runs_on if r[2].endswith("-latest")], [r for r in runs_on if r[2].endswith("-latest")]
# Scenario 2: 전부 같은 고정 버전을 쓴다(작업마다 다르면 한쪽만 26.04로 넘어가는 일이 생긴다)
assert all(r[2] == PINNED for r in runs_on), runs_on
print(f"워크플로 러너 고정 OK — {len(runs_on)}곳 모두 {PINNED}")
