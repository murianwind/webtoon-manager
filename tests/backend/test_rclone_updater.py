"""rclone 자체 업데이트(app/rclone_updater.py) — 아카이빙이 시작될 때마다 최신 버전을 확인해서 다르면 바이너리를 교체한다.
핵심 약속: 어떤 실패(네트워크/형식/압축/교체)도 예외가 아니라 "기존 버전 유지" 메시지로 돌려준다 — 이 확인 때문에 아카이빙이 멈추면 안 된다.
"""
import asyncio
import io
import os
import platform
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app import rclone_updater as ru
from fakes import AsyncResponse

# Scenario 1: 현재 버전 — 첫 줄 "rclone v1.75.0-322"에서 태그만. 실패/이상한 출력은 None
class Done:
    def __init__(self, stdout="", code=0): self.stdout, self.returncode = stdout, code
def with_run(result):
    def fake(cmd, **kw):
        fake.cmd = cmd
        if isinstance(result, BaseException): raise result
        return result
    ru.subprocess.run = fake; return fake
f = with_run(Done("rclone v1.75.0-322\n- os/version: alpine\n")); assert ru.get_current_version("/x/rclone") == "v1.75.0-322" and f.cmd == ["/x/rclone", "--version"]
for bad in (Done("rclone v1.0", 1), Done(""), Done("이상한 출력\n"), FileNotFoundError(), subprocess.TimeoutExpired("rclone", 10)):
    with_run(bad); assert ru.get_current_version() is None, bad
print("1) 현재 버전 OK")

# Scenario 2: 구조(CPU) 이름 — 알려진 것만 바꾸고 모르면 amd64
for machine, expected in (("x86_64", "amd64"), ("AMD64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64"), ("armv7l", "arm-v7"), ("armv6l", "arm-v6"), ("riscv64", "amd64")):
    platform.machine = lambda m=machine: m; assert ru._detect_arch() == expected, machine
platform.machine = lambda: "x86_64"
print("2) 구조 이름 OK")

# ── 가짜 GitHub: url → 응답(또는 예외) ──
LATEST = ru._LATEST_RELEASE_API
script = {}; requested = []
class FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, timeout=None):
        requested.append((url, timeout.total))
        outcome = script[url]
        if isinstance(outcome, BaseException): raise outcome
        return outcome
ru.aiohttp.ClientSession = FakeClient
def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items(): z.writestr(name, data)
    return buf.getvalue()
URL = "https://github.com/wiserain/rclone/releases/download/v2.0.0-1/rclone-v2.0.0-1-linux-amd64.zip"
tmp = Path(tempfile.mkdtemp()); binary = tmp / "rclone"
def update(current="v1.0.0", binary_path=binary):
    ru.get_current_version = lambda b="rclone": current
    requested.clear()
    return asyncio.run(ru.check_and_update(str(binary_path)))

# Scenario 3: 최신 버전 확인 단계의 실패 — HTTP 오류/네트워크 오류/태그 없음 → 기존 버전 유지 메시지, 최신이면 "이미 최신"(다운로드 안 함)
script.clear(); script[LATEST] = AsyncResponse(503); message = update(); assert "확인 실패 (HTTP 503)" in message and "기존 버전(v1.0.0) 유지" in message and requested[0][1] == 30, message
script[LATEST] = ConnectionError("DNS 실패"); message = update(); assert "확인 실패 (DNS 실패)" in message and "유지" in message
script[LATEST] = AsyncResponse(200, {}); assert "정보를 못 읽음" in update()
script[LATEST] = AsyncResponse(200, {"tag_name": "v2.0.0-1"}); message = update(current="v2.0.0-1"); assert message == "rclone 이미 최신 버전 (v2.0.0-1)" and len(requested) == 1, (message, requested)
print("3) 최신 확인 단계 OK")

# Scenario 4: 다운로드 단계의 실패 — 주소는 태그와 구조로 만든다(제한 시간은 확인의 4배). HTTP 오류/네트워크 오류 → 유지
script[LATEST] = AsyncResponse(200, {"tag_name": "v2.0.0-1"}); script[URL] = AsyncResponse(404)
message = update(); assert "다운로드 실패 (HTTP 404)" in message and requested[1] == (URL, 120), requested
script[URL] = TimeoutError("시간 초과"); assert "다운로드 실패 (시간 초과)" in update()
print("4) 다운로드 단계 OK")

# Scenario 5: 압축 풀기/교체 단계의 실패 — 깨진 zip, 폴더가 없는 zip, 폴더는 있는데 rclone이 없는 zip → 모두 유지 메시지(원래 파일은 그대로)
binary.write_bytes(b"OLD")
script[URL] = AsyncResponse(200, body="zip이 아님".encode()); message = update(); assert "바이너리 교체 실패" in message and "기존 버전(v1.0.0) 유지" in message and binary.read_bytes() == b"OLD", message
script[URL] = AsyncResponse(200, body=zip_bytes({"README.txt": "폴더 없음"})); assert "압축 해제 실패" in update() and binary.read_bytes() == b"OLD"
script[URL] = AsyncResponse(200, body=zip_bytes({"rclone-v2.0.0-1-linux-amd64/other": "x"})); assert "파일 구성이 예상과 달라 건너뜀" in update() and binary.read_bytes() == b"OLD"
print("5) 압축/구성 실패 OK")

# Scenario 6: 성공 — 새 바이너리로 원자적으로 교체하고(실행 권한 755, 임시 파일 .new 남지 않음) "현재 → 최신" 메시지. 현재 버전을 몰라도(None) 교체한다
script[URL] = AsyncResponse(200, body=zip_bytes({"rclone-v2.0.0-1-linux-amd64/rclone": "NEW BINARY"}))
message = update(current="v1.0.0"); assert message == "rclone 업데이트 완료: v1.0.0 → v2.0.0-1", message
assert binary.read_bytes() == b"NEW BINARY" and stat.S_IMODE(os.stat(binary).st_mode) == 0o755 and not (tmp / "rclone.new").exists() and not list(tmp.glob("*.new"))
binary.write_bytes(b"OLD"); message = update(current=None); assert "None → v2.0.0-1" in message and binary.read_bytes() == b"NEW BINARY"
print("ALL OK")
