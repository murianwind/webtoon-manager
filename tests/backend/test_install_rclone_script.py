"""Docker 빌드의 rclone 설치(scripts/install_rclone.sh) — GitHub API가 막혀(403) 최신 버전을 못 알아내도 "이미 설치됨"으로 착각해 넘어가지 않는다.
최신 버전은 API 대신 릴리즈 페이지 이동 주소에서 알아내 설치 스크립트에 직접 넘기고, 실패하면 재시도하고, 끝내 못 하면 빌드를 실패시킨다. 설치 뒤에는 rclone이 실제로 돌아가는지 확인한다.
(가짜 curl/rclone으로 확인한다 — 네트워크를 쓰지 않는다.)"""
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_rclone.sh"

FAKE_CURL = r"""#!/bin/sh
# 태그 조회(-w가 있는 호출)와 설치 스크립트 내려받기를 흉내 낸다. 호출 기록은 $LOG에 남긴다.
case "$*" in
*-w*)
  echo lookup >> "$LOG"
  n=$(grep -c lookup "$LOG")
  if [ "$n" -le "${LOOKUP_FAILS:-0}" ]; then echo "curl: (22) The requested URL returned error: 403" >&2; printf 'https://github.com/wiserain/rclone/releases/latest'; exit 22; fi
  printf 'https://github.com/wiserain/rclone/releases/tag/v1.99.0-mod1.2.3'
  ;;
*)
  echo fetch-installer >> "$LOG"
  cat <<'INSTALLER'
echo "installer args: $*" >> "$LOG"
n=$(grep -c fetch-installer "$LOG")
if [ "$n" -le "${INSTALL_FAILS:-0}" ]; then exit 1; fi
if [ -n "${INSTALL_EXIT3:-}" ]; then exit 3; fi
printf '#!/bin/sh\necho "rclone v1.99.0-mod1.2.3"\n' > "$BIN/rclone"; chmod +x "$BIN/rclone"
INSTALLER
  ;;
esac
"""


def run(**env_overrides):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp); bin_dir = tmp / "bin"; bin_dir.mkdir()
        (bin_dir / "curl").write_text(FAKE_CURL); (bin_dir / "curl").chmod(0o755)
        log = tmp / "log"; log.write_text("")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(log), "BIN": str(bin_dir), "RCLONE_WAIT": "0", "RCLONE_TRIES": "3", **env_overrides}
        env.pop("RCLONE_PRESENT", None)
        if env_overrides.get("RCLONE_PRESENT"):
            (bin_dir / "rclone").write_text('#!/bin/sh\necho "rclone v1.99.0-mod1.2.3"\n'); (bin_dir / "rclone").chmod(0o755)
        result = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        return result.returncode, log.read_text().splitlines(), (bin_dir / "rclone").exists(), result.stdout + result.stderr


# Scenario 1: 정상 — 버전을 한 번에 알아내 설치 스크립트에 그 버전을 넘기고, 설치 확인까지 통과한다
code, log, installed, out = run()
assert code == 0 and installed, out
assert "installer args: v1.99.0-mod1.2.3" in log and log.count("lookup") == 1, log

# Scenario 2: 버전 조회가 두 번 막혀도(403) 재시도해서 설치한다
code, log, installed, out = run(LOOKUP_FAILS="2")
assert code == 0 and installed and log.count("lookup") == 3 and "installer args: v1.99.0-mod1.2.3" in log, (code, log, out)

# Scenario 3: 끝내 버전을 못 알아내면 설치를 시도하지 않고 빌드를 실패시킨다("이미 설치됨"으로 착각해 통과하지 않는다)
code, log, installed, out = run(LOOKUP_FAILS="99")
assert code != 0 and not installed and log.count("lookup") == 3 and "fetch-installer" not in log, (code, log, out)

# Scenario 4: 설치 스크립트가 한 번 실패해도 재시도하고, 끝내 실패하면 빌드를 실패시킨다
code, log, installed, out = run(INSTALL_FAILS="1")
assert code == 0 and installed and log.count("fetch-installer") == 2, (code, log, out)
code, log, installed, out = run(INSTALL_FAILS="99")
assert code != 0 and not installed and log.count("fetch-installer") == 3, (code, log, out)

# Scenario 5: 설치 스크립트가 "이미 최신"(종료 코드 3)을 알려도 rclone이 실제로 있으면 성공이고, 없으면 실패다
code, log, installed, out = run(INSTALL_EXIT3="1", RCLONE_PRESENT="1")
assert code == 0, (code, log, out)
code, log, installed, out = run(INSTALL_EXIT3="1")
assert code != 0 and not installed, (code, log, out)
print("ALL OK")
