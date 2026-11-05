"""실제 네트워크를 부르는 경로를 가짜로 바꾸지 않고 작업을 돌리는 테스트를 막는다.
아카이빙 작업(/api/archive/run, run_archive_job)은 시작할 때 rclone 업데이트 확인(rclone_updater.check_and_update)을 먼저 하는데, 이것이 GitHub에 실제로 접속하고 새 버전이 있으면
바이너리를 내려받아 교체하기까지 한다. 네트워크가 막힌 곳(개발 샌드박스)에서는 즉시 실패해서 테스트가 통과하지만, 네트워크가 되는 곳(CI)에서는 오래 걸려서 테스트가 작업이 끝나기 전에
결과를 확인하다 실패한다(실제로 test_kp_archive_kakao가 CI에서만 실패했다). 그래서 이 작업을 돌리는 테스트가 check_and_update를 가짜로 바꾸는지 소스를 보고 확인한다."""
import re
from pathlib import Path

STARTS_ARCHIVE_JOB = re.compile(r"archive/run|run_archive_job")
offenders = []
for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    if path.name == Path(__file__).name:
        continue
    source = path.read_text(encoding="utf-8")
    if STARTS_ARCHIVE_JOB.search(source) and "check_and_update" not in source:
        offenders.append(path.name)
assert not offenders, f"아카이빙 작업을 돌리면서 rclone 업데이트 확인(check_and_update)을 가짜로 바꾸지 않은 테스트(실제 GitHub 접속): {offenders}"
print("ALL OK")
