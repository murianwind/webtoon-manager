"""카카오 수동 다운로드 분석 응답(GET /api/kakao-manual/analyze) — 응답 전체를 시나리오별로 고정한다(골든 파일).
이 응답은 분석 화면이 쓰는 모든 값(폴더 규칙 결과, 회차별 상태/선택 가능 여부, 이용권, 진단 수치)을 담은 큰 사전이라 한두 필드만 보는 테스트로는 바뀐 곳을 놓친다.
시나리오: 로그인+대여권, 익명(쿠키 없음), 폴더에 받은 파일/받은 기록/보관으로 옮긴 회차가 섞인 경우, 기다무 충전, 목록 못 읽음(502).
골든 파일: tests/fixtures/kakao_analyze_golden.json (임시 폴더 경로는 <ROOT>로 바꿔 비교). 응답 모양을 일부러 바꿀 때만 UPDATE_GOLDEN=1로 다시 만든다."""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import aiohttp
import httpx

from app import db, repository
from app import kakao_page_auth as auth
from app import kakao_page_download as kp
from fake_kakao_rental_site import SITE, FakeSession, Site, item
db.get_connection()
import app.main as m

aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "kakao_analyze_golden.json"
ROOT = tempfile.mkdtemp()
COOKIES = json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])


def normalize(value):
    """임시 폴더 경로를 <ROOT>로 — 응답 전체를 문자열로 바꿔 치환한다."""
    return json.loads(json.dumps(value, ensure_ascii=False).replace(ROOT, "<ROOT>"))


async def analyze(c, series_id=1):
    r = await c.get("/api/kakao-manual/analyze", params={"series_id": series_id})
    return {"status": r.status_code, "body": normalize(r.json())}


async def main():
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, ROOT)
    results = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # 1. 로그인(쿠키 저장) + 대여권 2장, 기다무 충전 전: 무료/잠금(기다무 대기)/기다무 불가(대여권 대상)/숨김
        auth.save_cookies(auth.parse_cookie_export(COOKIES))
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True), item(4, blocked=True, hidden=True)], rental=2, waitfree=False)
        results["로그인+대여권"] = await analyze(c)
        # 2. 기다무가 충전돼 있고 대여권은 없다
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=0, ticket_type=None, waitfree=True)
        results["기다무 충전"] = await analyze(c)
        # 3. 작품에 기다무가 없다
        SITE["site"] = Site([item(1, free=True), item(2)], rental=1, series_waitfree=False)
        results["기다무 없는 작품"] = await analyze(c)
        # 4. 폴더에 받은 파일이 있고 받은 회차 기록에는 더 있다(보관 폴더로 옮긴 회차) + 받지 않은 것으로 기록한 회차
        SITE["site"] = Site([item(i, title=f"작품 {i}화", free=True) for i in range(1, 7)], rental=0)
        folder = kp.series_folder(ROOT, "작품")          # 이 가짜 사이트의 작품 제목
        folder.mkdir(parents=True, exist_ok=True)
        for n in (3, 4): (folder / f"{n}_{n}화.zip").write_bytes(b"x")                      # 폴더에 받은 파일(번호_부제목.zip)
        repository.upsert_new_kakao_webtoon(1, "작품"); repository.set_kakao_downloaded_numbers(1, [1, 2, 3, 4]); repository.set_kakao_pending_numbers(1, [2])
        results["폴더/기록 섞임"] = await analyze(c)
        # 5. 익명(쿠키 없음) — 로그인/이용권 정보 없음
        auth.clear_cookies() if hasattr(auth, "clear_cookies") else repository.set_setting(auth.COOKIE_SETTING_KEY if hasattr(auth, "COOKIE_SETTING_KEY") else "kakao_page_cookies", None)
        SITE["site"] = Site([item(1, free=True), item(2)])
        results["익명"] = await analyze(c, series_id=2)                                  # 앞 시나리오의 받은 기록과 섞이지 않게 다른 작품 번호
        # 5b. 쿠키는 저장돼 있지만 로그인이 풀려 있다 — 이용권은 조회하지 않는다(요청도 안 간다)
        auth.save_cookies(auth.parse_cookie_export(COOKIES))
        async def logged_out(self): return False
        real_check = kp.KakaoPageClient.check_login; kp.KakaoPageClient.check_login = logged_out
        SITE["site"] = Site([item(1, free=True), item(2)], rental=3, waitfree=True)
        results["로그인 풀림"] = await analyze(c, series_id=2); assert not any(g.endswith("ticket/my") for g in SITE["site"].gets), "로그아웃이면 이용권 조회를 하지 않는다"
        kp.KakaoPageClient.check_login = real_check
        # 5c. 작품에 기다무가 없는데 이용권 응답은 "충전됨"이라고 한다 — 기다무는 쓸 수 없다(waitfree_supported false, 회차 상태도 기다무가 아니다)
        SITE["site"] = Site([item(1, free=True), item(2), item(3, blocked=True)], rental=1, waitfree=True, series_waitfree=False)
        results["기다무 없는 작품(충전 표시)"] = await analyze(c, series_id=2)
        # 5d. 이용권 응답에 충전 주기가 없으면 작품 정보의 주기를 쓴다
        SITE["site"] = Site([item(1, free=True), item(2)], ticket_period=0, series_period=180); results["충전 주기는 작품 정보 값"] = await analyze(c, series_id=2)
        # 5e. 폴더에 받은 파일이 하나뿐이고 받은 기록이 없다 — 그 파일이 "여기까지 받았다"는 표식이다
        (folder / "4_4화.zip").unlink()
        SITE["site"] = Site([item(i, title=f"작품 {i}화", free=True) for i in range(1, 7)]); results["파일 하나(표식)"] = await analyze(c, series_id=3)
        # 6. 목록을 못 읽으면 502
        class Broken(Site):
            def get(self, url, params=None, headers=None, timeout=None):
                r = super().get(url, params=params, headers=headers, timeout=timeout)
                if "product/list" in url: r.status = 500
                return r
        SITE["site"] = Broken([item(1)]); results["목록 실패"] = await analyze(c)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"); print("골든 파일을 새로 만들었다:", GOLDEN)
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(results) == set(golden), (set(results) ^ set(golden))
    for name in golden:
        assert results[name] == golden[name], f"{name}: 응답이 골든과 다르다\n  실제: {json.dumps(results[name], ensure_ascii=False)[:600]}\n  골든: {json.dumps(golden[name], ensure_ascii=False)[:600]}"
        print("  ✓", name)
asyncio.run(main())
print("ALL OK")
