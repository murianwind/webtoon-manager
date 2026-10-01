"""문서/배포 설정 검사 — README는 일반 사용자용 설명서이고, 설치 안내(YAML)는 docker-compose.yml과 같고, 도움말 탭에서 깨지지 않고,
깃헙 액션은 "테스트 통과 후에만 빌드 + 새 실행이 오면 이전 실행 취소"를 지킨다. (어긋나면 CI가 실패해서 바로 알 수 있다)"""
import asyncio
import re
from pathlib import Path

import httpx
import yaml

from app import db, help_page

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["webtoon-manager"]

# ── 1. README의 설치용 YAML = docker-compose.yml ──
blocks = re.findall(r"^```yaml\n(.*?)^```", README, re.S | re.M)
assert len(blocks) == 1, "설치 안내에 YAML 블록이 정확히 하나 있어야 합니다"
readme_service = yaml.safe_load(blocks[0])["services"]["webtoon-manager"]
for key in ("image", "container_name", "restart", "labels", "ports", "environment", "volumes"):
    assert readme_service.get(key) == COMPOSE.get(key), f"README의 설치 YAML과 docker-compose.yml이 다릅니다: {key}\n README={readme_service.get(key)}\n compose={COMPOSE.get(key)}"
# compose가 쓰는 ${변수}는 README의 환경변수 표에 모두 설명돼 있어야 한다(기본값이 있는 WEB_PORT/COOKIE_FILE_NAME 포함)
compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
variables = sorted(set(re.findall(r"\$\{([A-Z_]+)", "\n".join(l for l in compose_text.split("\n") if not l.strip().startswith("#")))))
missing = [v for v in variables if f"`{v}`" not in README]
assert not missing, f"README 환경변수 표에 설명이 없는 변수: {missing}"
print(f"1) 설치 YAML = compose OK (환경변수 {len(variables)}개 설명)")

# ── 2. README는 일반 사용자용 — 개발자 용어/파일 경로가 없다 ──
FORBIDDEN = ("pyflakes", "pytest", "run_backend", "jsdom", "npm ", "tests/", "app/", "tempfile", "fixture", "특성 테스트", "단일 책임", "깃헙 액션",
             "GitHub Actions", "viewer/data", "on_issue", "is_waitfree", "RT05", "`hidden`", "token=", "signature=", "kakao_<", "API", "DB ", "HAR")
FORBIDDEN_PATTERNS = (r"\.py\b", r"\.js\b")             # 코드 파일 이름(cookies.json 같은 설정 파일 이름은 허용)
problems = [f for f in FORBIDDEN if f in README] + [p for p in FORBIDDEN_PATTERNS if re.search(p, README)]
assert not problems, f"README(사용자 설명서)에 개발자 용어가 있습니다: {problems} — 개발 내용은 DEVELOPMENT.md로"
dev = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")
assert "테스트" in dev and "깃헙 액션" in dev and "README" in dev, "개발 문서가 비어 있습니다"
print("2) README 사용자용 OK / DEVELOPMENT.md 존재")

# ── 3. 도움말 탭 렌더링: 코드 블록/표/접기/목차/내부 링크가 깨지지 않는다 ──
html = help_page.render_help(README)
fences = len(re.findall(r"^```", README, re.M)) // 2
assert html.count("<pre>") == fences and "```" not in html, f"코드 블록이 도움말에서 깨집니다(펜스 {fences}개, <pre> {html.count('<pre>')}개)"
assert html.count("<table>") == len(re.findall(r"^\|[-: |]+\|$", README, re.M)), "표가 도움말에서 깨집니다"
faq = len(re.findall(r"^<details", README, re.M)); assert faq >= 8 and html.count("<details") == faq and html.count("<summary>") == faq, "자주 묻는 질문이 접기 형태로 렌더링되지 않습니다"
headings = re.findall(r"^## (.+)$", README, re.M)
nav = re.search(r'<nav class="help-toc">(.*?)</nav>', html, re.S); assert nav, "목차가 없습니다"
for h in headings: assert h.split(" — ")[0] in nav.group(1), f"목차에 없는 섹션: {h}"
ids = set(re.findall(r'id="([^"]+)"', html))
for href in re.findall(r"\]\(#([^)]+)\)", README): assert href in ids, f"README의 내부 링크가 도움말에서 이동할 곳이 없습니다: #{href}"
for href in re.findall(r'<a href="#([^"]+)"', nav.group(1)): assert href in ids, f"목차 링크가 가리킬 곳이 없습니다: #{href}"
assert "<script" not in html.lower() and "onerror" not in html.lower()
print(f"3) 도움말 렌더링 OK (코드블록 {fences}, 접기 {faq}, 섹션 {len(headings)}개 목차)")

# ── 4. 깃헙 액션: 테스트 통과 후에만 빌드, 새 실행이 오면 이전 실행 취소 ──
def workflow(name):
    data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    data["triggers"] = data.get("on", data.get(True))        # YAML 1.1에서 on이 True로 읽힌다
    return data
build, tests = workflow("build.yml"), workflow("tests.yml")
jobs = build["jobs"]
call = [name for name, job in jobs.items() if job.get("uses") == "./.github/workflows/tests.yml"]
assert len(call) == 1, "build.yml이 tests.yml을 불러서 먼저 테스트해야 합니다"
image_jobs = [name for name, job in jobs.items() if "steps" in job and any("build-push-action" in str(s.get("uses", "")) for s in job["steps"])]
assert len(image_jobs) == 1 and call[0] in jobs[image_jobs[0]]["needs"], "이미지 빌드는 테스트 job이 끝난 뒤에만(needs) 실행돼야 합니다"
assert jobs[image_jobs[0]]["permissions"]["packages"] == "write"
assert set(build["triggers"]) == {"push", "workflow_dispatch"} and build["triggers"]["push"]["branches"] == ["main"], "빌드는 main 푸시(와 수동 실행)에서만"
assert "workflow_call" in tests["triggers"] and "pull_request" in tests["triggers"]
assert "main" in tests["triggers"]["push"]["branches-ignore"], "main 푸시는 build.yml이 테스트하므로 tests.yml이 중복으로 돌지 않게"
for name, wf in (("build.yml", build), ("tests.yml", tests)):
    conc = wf.get("concurrency") or {}
    assert conc.get("cancel-in-progress") is True and "github.ref" in str(conc.get("group")), f"{name}: 새 실행이 오면 이전 실행을 취소해야 합니다"
assert "github.event_name" in tests["concurrency"]["group"] and tests["concurrency"]["group"] != build["concurrency"]["group"], "불려 쓰는 쪽과 그룹 이름이 같으면 서로 취소해 버립니다"
print("4) 깃헙 액션 OK (테스트 통과 후 빌드, 이전 실행 취소)")

# ── 5. 도움말 탭이 쓰는 엔드포인트(/api/help)가 같은 결과를 준다 ──
db.get_connection()
import app.main as app_main


async def fetch_help():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_main.app), base_url="http://test") as client:
        return await client.get("/api/help")


response = asyncio.run(fetch_help())
assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
assert response.text == help_page.render_help(README), "엔드포인트가 README를 그대로 변환해서 줘야 합니다"
assert 'class="help-toc"' in response.text and "<pre>" in response.text and "<details>" in response.text
print("5) /api/help OK")
print("\n전부 통과")
