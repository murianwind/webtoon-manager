"""
rclone 자체 업데이트. 도커 이미지는 빌드 시점의 rclone 버전으로 고정되는데, 대부분의
사용자가 스택을 자주 재배포하지 않아서 그 버전에 계속 머무르는 문제가 있었다 —
그래서 이미지 재빌드/재배포에 기대지 않고, **아카이빙 주기가 시작될 때마다 앱이
직접 최신 버전을 확인해서 필요하면 바이너리를 교체**한다.

네트워크/GitHub API 오류가 나도 절대 아카이빙 자체를 막으면 안 된다 — 버전 확인은
어디까지나 부가 기능이라, 실패하면 그냥 기존 버전으로 계속 진행한다.
"""

import logging
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

_LATEST_RELEASE_API = "https://api.github.com/repos/wiserain/rclone/releases/latest"
_TIMEOUT_SECONDS = 30


def get_current_version(rclone_binary: str = "rclone") -> str | None:
    """설치된 rclone의 버전 태그(예: 'v1.75.0-322')를 반환한다. 실패하면 None."""
    try:
        result = subprocess.run([rclone_binary, "--version"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    match = re.match(r"rclone\s+(\S+)", first_line)
    return match.group(1) if match else None


def _detect_arch() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
        "armv7l": "arm-v7", "armv6l": "arm-v6",
    }
    return mapping.get(machine, "amd64")


async def check_and_update(rclone_binary: str = "/usr/bin/rclone") -> str:
    """최신 태그를 확인해서, 현재 버전과 다르면 바이너리를 교체한다.
    반환값은 사람이 읽을 결과 메시지 — 실패해도 예외를 던지지 않고 메시지로 알린다
    (아카이빙 잡이 이것 때문에 중단되면 안 되므로)."""
    current = get_current_version(rclone_binary)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(_LATEST_RELEASE_API, timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)) as response:
                if response.status != 200:
                    return f"rclone 최신버전 확인 실패 (HTTP {response.status}) — 기존 버전({current}) 유지"
                data = await response.json()
    except Exception as e:
        return f"rclone 최신버전 확인 실패 ({e}) — 기존 버전({current}) 유지"

    latest_tag = data.get("tag_name")
    if not latest_tag:
        return f"rclone 최신버전 정보를 못 읽음 — 기존 버전({current}) 유지"

    if current == latest_tag:
        return f"rclone 이미 최신 버전 ({current})"

    arch = _detect_arch()
    asset_name = f"rclone-{latest_tag}-linux-{arch}.zip"
    download_url = f"https://github.com/wiserain/rclone/releases/download/{latest_tag}/{asset_name}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS * 4)) as response:
                if response.status != 200:
                    return f"rclone 업데이트 다운로드 실패 (HTTP {response.status}) — 기존 버전({current}) 유지"
                content = await response.read()
    except Exception as e:
        return f"rclone 업데이트 다운로드 실패 ({e}) — 기존 버전({current}) 유지"

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / asset_name
            zip_path.write_bytes(content)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)
            extracted_dirs = [p for p in Path(tmp_dir).iterdir() if p.is_dir()]
            if not extracted_dirs:
                return f"rclone 업데이트 압축 해제 실패 — 기존 버전({current}) 유지"
            new_binary = extracted_dirs[0] / "rclone"
            if not new_binary.exists():
                return f"rclone 업데이트 파일 구성이 예상과 달라 건너뜀 — 기존 버전({current}) 유지"

            target = Path(rclone_binary)
            tmp_target = target.with_suffix(".new")
            shutil.copy(new_binary, tmp_target)
            tmp_target.chmod(0o755)
            tmp_target.replace(target)  # 원자적 교체
    except Exception as e:
        return f"rclone 바이너리 교체 실패 ({e}) — 기존 버전({current}) 유지"

    return f"rclone 업데이트 완료: {current} → {latest_tag}"
