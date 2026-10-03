"""카카오페이지 라우트 테스트 — 다운로드 폴더 설정, 자동 다운로드 스위치, 수동 다운로드(검색/분석/받기), 백업."""
import asyncio, json, os, tempfile, time, zipfile
from http.cookies import SimpleCookie
from pathlib import Path
import aiohttp, httpx

from app import db, repository
from app import kakao_page_auth as auth, kakao_page_download as kp
from app.config import get_settings
db.get_connection()
import app.main as m

S = {"profile": "ok", "eps": None, "fail_list": False, "fail_images": False, "search": []}
SERIES = "닥터 최태수"
class FakeResp:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
def ep(order, title, free=True, rent=False, hidden=False):
    pi = {"purchase_type": "rent", "rent_expire_dt": "2099-01-01 00:00:00"} if rent else {"purchase_type": "not_purchased"}
    return {"cursor_index": order, "item": {"product_id": 7000 + order, "title": title, "is_free": free, "order_value": order, "page_count": 2, "hidden": hidden, "service_property": {"purchase_info": pi}}}
def default_eps(): return [ep(1, "프롤로그"), ep(2, f"{SERIES} 1화"), ep(3, f"{SERIES} 2화"), ep(4, f"{SERIES} 3화", free=False, rent=True), ep(5, f"{SERIES} 4화", free=False)]
def handler(method, url, params, data):
    if url.endswith("/user/get_profile"):
        return FakeResp(200, {"result_code": 0, "profile": {"uid": 1}}) if S["profile"] == "ok" else (FakeResp(200, {"result_code": 401}) if S["profile"] == "out" else FakeResp(403))
    if "product/list" in url:
        if S["fail_list"]: return FakeResp(500)
        return FakeResp(200, {"result": {"series_item": {"title": SERIES, "is_waitfree": True}, "list": S["eps"] or default_eps(), "has_next": False}})
    if "viewer/data" in url:
        files = [{"no": i, "secureUrl": f"https://page-edge.kakao.com/sdownload/resource?kid=k{params['product_id']}_{i}&signature=S"} for i in (1, 2)]
        return FakeResp(200, {"item": {}, "viewer_data": {"imageDownloadData": {"files": files}}})
    if "sdownload" in url: return FakeResp(500) if S["fail_images"] else FakeResp(200, body=b"\xff\xd8\xffx", ctype="image/jpeg")
    if "search/series" in url: return FakeResp(200, {"result": {"list": S["search"], "is_end": True}})
    if "dayofweek" in url: return FakeResp(200, {"result": {"list": [], "is_end": True}})
    if url.endswith("/ticket/my"):
        return FakeResp(200, {"result_code": 0, "result": {"waitfree": {"charged_complete": S.get("waitfree", False), "charged_at": "2026-10-01T18:41:44+09:00"},
                                                           "my": {"ticket_own_count": 0, "ticket_rental_count": 2}}})
    if "ready_to_use" in url: return FakeResp(200, {"result_code": 0, "result": {"single": {"waitfree_block": False}, "available": {"ticket_rental_type": "RT05"}}})
    if url.endswith("/ticket/use"): S.setdefault("used", []).append(dict(data or {})); return FakeResp(200, {"result_code": 0, "result": {"ticket_uid": "1"}})
    if "content/about" in url: return FakeResp(200, {"result_code": 0, "result": {"author_list": [{"name": "글", "role": "writer"}], "theme_keyword_list": []}})
    raise AssertionError(url)
class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, cookies=None, timeout=None):
        url = str(url); return handler("GET", url, dict(params or {}), None)
    def post(self, url, data=None, headers=None, timeout=None): return handler("POST", url, {}, data)
aiohttp.ClientSession = FakeSession
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"
_orig_sleep = asyncio.sleep
async def _nosleep(x): await _orig_sleep(0)
kp.asyncio.sleep = _nosleep
alerts = []
async def fake_send(session, settings, message): alerts.append(message)
auth.discord_notify.send_webhook_notification = fake_send
auth.discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")   # 실제처럼 웹훅이 있어야 쿠키 알림이 나간다(없으면 보내지도 기록하지도 않음)

def export(days=25): return json.dumps([{"domain": ".kakao.com", "name": n, "value": f"val-{n}", "path": "/", "expirationDate": time.time() + days * 86400} for n in auth.REQUIRED_COOKIES])
DEFAULT_ROOT = Path(os.environ["DOWNLOAD_ROOT"]); KROOT = Path(tempfile.mkdtemp())
async def wait_manual(c, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = (await c.get("/api/jobs/status")).json().get("manual") or {}
        if st.get("status") and st["status"] != "running": return st
        await _orig_sleep(0.02)
    raise AssertionError("수동 다운로드가 안 끝남")

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 1. 다운로드 폴더 설정 (네이버/카카오페이지 따로) ──
        NROOT = KROOT.parent / "naver_dl"; NROOT.mkdir(exist_ok=True)
        r = (await c.get("/api/settings/download-roots")).json()
        assert r["naver"]["path"] == "" and r["naver"]["effective"] == str(DEFAULT_ROOT) and r["kakao"]["path"] == "" and r["kakao"]["effective"] == str(DEFAULT_ROOT)   # 안 정하면 둘 다 기본 폴더
        assert (await c.post("/api/settings/download-roots", json={"kakao": "relative/dir"})).status_code == 400
        assert (await c.post("/api/settings/download-roots", json={"naver": "relative/dir"})).status_code == 400
        r = await c.post("/api/settings/download-roots", json={"kakao": str(KROOT / "없는폴더")})
        assert r.status_code == 400 and "마운트" in r.json()["detail"] and not (KROOT / "없는폴더").exists()          # 없는 폴더를 자동으로 만들지 않는다
        r = await c.post("/api/settings/download-roots", json={"naver": str(NROOT), "kakao": str(KROOT)})
        r = r.json(); assert r["naver"]["effective"] == str(NROOT) and r["kakao"]["effective"] == str(KROOT)          # 완전히 다른 폴더
        r = (await c.post("/api/settings/download-roots", json={"naver": str(NROOT), "kakao": ""})).json()
        assert r["kakao"]["effective"] == str(NROOT)                                                                   # 카카오를 비우면 네이버 폴더를 쓴다
        from app import download_roots
        st = get_settings()
        assert download_roots.naver_root(st) == str(NROOT) and download_roots.kakao_root(st) == str(NROOT)
        await c.post("/api/settings/download-roots", json={"naver": str(NROOT), "kakao": str(KROOT)})
        assert [download_roots.local_root_path(n, st) for n in ("archive", "download", "kakao_download")] == [st.archive_root, str(NROOT), str(KROOT)]   # 아카이빙 폴더 이름표
        for name in ("download", "kakao_download", "archive"):                                                        # 아카이빙 폴더 탐색이 세 가지를 모두 받는다
            assert (await c.get("/api/archive/folders", params={"local_root": name})).status_code in (200, 400), name
        assert (await c.get("/api/archive/folders", params={"local_root": "nope"})).status_code == 400
        await c.post("/api/settings/download-roots", json={"naver": "", "kakao": str(KROOT)})
        assert download_roots.naver_root(st) == str(DEFAULT_ROOT)                                                     # 네이버를 비우면 환경변수 기본값
        # 폴더 선택기: 마운트된 다운로드 폴더(DOWNLOAD_ROOT) 안에서 찾아보고 새 폴더를 만든다(아카이빙 폴더 API 재사용)
        base = Path(get_settings().download_root); base.mkdir(parents=True, exist_ok=True); (base / "Naver").mkdir(exist_ok=True)
        r = (await c.get("/api/archive/folders", params={"local_root": "download_base", "path": ""})).json()
        assert "Naver" in [f["name"] for f in r["folders"]]
        await c.post("/api/settings/download-roots", json={"naver": str(NROOT), "kakao": ""})                            # 네이버 폴더를 바꿔도
        r = (await c.get("/api/archive/folders", params={"local_root": "download_base", "path": ""})).json()
        assert "Naver" in [f["name"] for f in r["folders"]]                                                             # 찾아보기의 출발점은 고정(선택을 따라 움직이지 않음)
        assert (await c.post("/api/archive/folders", json={"root": "download_base", "path": "Kakao/개미모음"})).status_code == 200
        assert (base / "Kakao" / "개미모음").is_dir()                                                                     # 새 폴더 만들기
        assert (await c.post("/api/archive/folders", json={"root": "download_base", "path": "../밖으로"})).status_code == 400   # 마운트 밖으로는 못 나감
        assert (await c.get("/api/archive/folders", params={"local_root": "download_base", "path": "../.."})).status_code == 400
        st_ = (await c.get("/api/settings/download-roots")).json(); assert st_["base"] == str(base) and st_["host_path"] == ""
        os.environ["WEBTOON_DOWNLOAD_HOST_PATH"] = r"D:\Downloads\Webtoon\Webtoon_Download"; get_settings.cache_clear()     # 호스트 경로를 알려주면 함께 보여준다
        await c.post("/api/settings/download-roots", json={"naver": str(base / "Kakao" / "개미모음"), "kakao": ""})
        st_ = (await c.get("/api/settings/download-roots")).json()
        assert st_["host_path"] == os.environ["WEBTOON_DOWNLOAD_HOST_PATH"] and st_["naver"]["effective_host"] == r"D:\Downloads\Webtoon\Webtoon_Download\Kakao\개미모음", st_["naver"]
        assert download_roots.host_path_of(str(base), get_settings()) == os.environ["WEBTOON_DOWNLOAD_HOST_PATH"] and download_roots.host_path_of("/다른곳/폴더", get_settings()) == ""
        del os.environ["WEBTOON_DOWNLOAD_HOST_PATH"]; get_settings.cache_clear()
        await c.post("/api/settings/download-roots", json={"naver": "", "kakao": str(KROOT)})
        print("1-2) 폴더 선택기 API OK (다운로드 폴더 기준 찾아보기/새 폴더, 밖으로 못 나감, 호스트 경로 표시)")
        print("1) 다운로드 폴더 설정 OK (네이버/카카오 따로, 절대경로만, 없는 폴더는 거부, 카카오를 비우면 네이버 폴더, 아카이빙 이름표 해석)")

        # ── 3. 수동 다운로드 검색 ──
        S["search"] = [
            {"series_id": 71000001, "category_uid": 10, "title": "선산", "authors": "연상호,최규석", "on_issue": "N", "thumbnail": "K/a/b", "age_grade": 15},
            {"series_id": 71000002, "category_uid": 10, "title": "계시록", "authors": "연상호", "on_issue": "Y", "thumbnail": "K/c/d"},
            {"series_id": 71000003, "category_uid": 10, "title": "휴재작", "authors": "누구", "on_issue": "P", "thumbnail": "K/e/f"},
            {"series_id": 71000004, "category_uid": 16, "title": "책은 제외", "authors": "누구", "on_issue": "N", "thumbnail": "K/g/h"}]
        r = (await c.get("/api/kakao-manual/search", params={"query": "연상호"})).json()
        assert [x["title"] for x in r] == ["선산", "계시록", "휴재작"] and [x["status"] for x in r] == ["완결", "연재", "휴재"]
        assert r[0]["authors"] == "연상호, 최규석" and r[0]["thumbnail_url"].startswith("https://page-images") and r[0]["title_id"] == 71000001
        assert (await c.get("/api/kakao-manual/search", params={"query": "  "})).status_code == 400
        repository.upsert_new_kakao_webtoon(int(S["search"][0]["series_id"]), "구독한 작품", status=repository.STATUS_ACTIVE) if S["search"] else None
        subs = [x["subscription"] for x in (await c.get("/api/kakao-manual/search", params={"query": "닥터"})).json()]
        assert subs and subs[0] == "active" and all(v in ("active", None) for v in subs), subs                       # 검색 결과가 이 프로그램의 구독 상태를 함께 준다
        with db.write_transaction() as cx: cx.execute("DELETE FROM kakao_webtoons WHERE title_id = ?", (int(S["search"][0]["series_id"]),))
        assert (await c.get("/api/kakao-manual/search", params={"query": "닥터"})).json()[0]["subscription"] is None
        print("3) 수동 검색 OK (웹툰만, 연재/완결/휴재 표시)")

        # ── 4. 분석: 스크린샷처럼 번호/제목/상태/대여만료/진행 ──
        S["profile"] = "ok"
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        assert r["title"] == SERIES and r["mode"] == "new_folder" and r["cookie_saved"] is False and r["logged_in"] is None
        assert r["folder"] == str(KROOT / SERIES) and r["existing_count"] == 0
        rows = {e["number"]: e for e in r["episodes"]}
        assert [e["number"] for e in r["episodes"]] == [1, 2, 3, 4, 5]
        assert rows[1]["subtitle"] == "프롤로그" and rows[2]["subtitle"] == "1화"
        assert (rows[2]["state"], rows[4]["state"], rows[5]["state"]) == ("free", "owned", "locked")
        assert rows[4]["expire"] == "2099-01-01 00:00:00" and rows[2]["expire"] is None
        assert rows[2]["downloaded"] is False and rows[2]["selectable"] is True and rows[5]["selectable"] is False
        assert (r["to_download_count"], r["locked_count"], r["downloaded_count"]) == (4, 1, 0)
        # 폴더 규칙: 여러 개면 비교, 하나면 표식
        folder = KROOT / SERIES; folder.mkdir(); (folder / "0002_1화#3.zip").write_bytes(b"x"); (folder / "0003_2화#3.zip").write_bytes(b"x"); (folder / "cover.jpg").write_bytes(b"x")
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        assert r["mode"] == "compare" and r["existing_count"] == 2 and r["downloaded_count"] == 2 and r["marker"] is None
        assert {e["number"]: e["downloaded"] for e in r["episodes"]} == {1: False, 2: True, 3: True, 4: False, 5: False}
        assert [e["selectable"] for e in r["episodes"]] == [True, True, True, True, False]                    # 이미 받은 회차도 다시 받을 수 있다(잠긴 것만 선택 불가)
        (folder / "0003_2화#3.zip").unlink()
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        assert r["mode"] == "single_marker" and r["marker"] == {"number": 2, "subtitle": "1화", "resolved_number": 2, "warning": False}
        by = {e["number"]: e for e in r["episodes"]}
        assert by[1]["downloaded"] is False and by[1]["before_start"] is True and by[1]["selectable"] is True     # 표식 앞 회차: 자동으론 안 받지만 직접 고르면 받을 수 있다
        assert by[2]["downloaded"] is True and r["before_start_count"] == 1
        (folder / "0002_1화#3.zip").unlink()
        # 로그인 상태 표시 + 풀림 알림
        await c.post("/api/settings/kakao-page-login", json={"cookies_json": export()})
        assert (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()["logged_in"] is True and alerts == []
        S["profile"] = "out"
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        assert r["logged_in"] is False and len(alerts) == 1 and "만료" in alerts[0]
        S["profile"] = "blocked"; assert (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()["logged_in"] is None
        S["profile"] = "ok"; S["fail_list"] = True
        assert (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).status_code == 502
        S["fail_list"] = False
        print("4) 분석 OK (번호/제목/상태/대여만료/진행, 규칙 3가지, 로그인 상태, 실패 시 502)")
        # ── 4-2. 이용권 표시 + 기다무 상태 + 구독 상태 ──
        S["waitfree"] = True
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        assert r["tickets"] == {"rental_count": 2, "own_count": 0, "waitfree_supported": True, "waitfree_ready": True, "waitfree_available_at": None, "waitfree_period_minutes": 0}
        by = {e["number"]: e for e in r["episodes"]}
        assert by[5]["state"] == "waitfree" and by[5]["selectable"] is True and by[4]["state"] == "owned" and by[2]["state"] == "free"     # 잠긴 회차는 기다무를 쓸 수 있으면 "기다무"
        assert r["subscription"] is None and "thumbnail_url" in r and "authors" in r
        S["waitfree"] = False
        r = (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()
        by = {e["number"]: e for e in r["episodes"]}
        assert r["tickets"]["waitfree_ready"] is False and r["tickets"]["waitfree_available_at"] and by[5]["state"] == "locked" and by[5]["selectable"] is False
        repository.upsert_new_kakao_webtoon(111, "닥터 최태수", status=repository.STATUS_ACTIVE)
        assert (await c.get("/api/kakao-manual/analyze", params={"series_id": 111})).json()["subscription"] == "active"                    # 구독 상태 표시
        S["waitfree"] = True; S["used"] = []
        assert (await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [5]})).status_code == 200
        st = await wait_manual(c)
        assert S["used"] == [{"product_id": 7005, "ticket_type": "RT05"}] and "기다무로 연 회차 1개" in "\n".join(st["log"]), st["log"][-4:]   # 골라서 받으면 기다무(RT05)로 열고 받는다
        S["waitfree"] = False; S["used"] = []
        folder_cleanup = KROOT / SERIES
        if folder_cleanup.exists():
            import shutil; shutil.rmtree(folder_cleanup)                                                              # 다음 구간이 처음 상태에서 시작하도록 되돌린다
        with db.write_transaction() as cx: cx.execute("DELETE FROM kakao_webtoons WHERE title_id = 111")
        repository.clear_episode_history()                                                                           # 이 구간이 남긴 받은 이력도 지운다
        print("4-2) 이용권/기다무 OK (대여권·소장권 수, 기다무 사용 가능/남은 시간, 기다무 회차 선택 가능, 구독 상태, 선택 받기 시 RT05만 사용)")

        # ── 5. 받기 ──
        await c.delete("/api/settings/kakao-page-login")
        r = await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [1, 2]}); assert r.status_code == 400 and "쿠키" in r.json()["detail"]
        assert (await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": []})).status_code == 422
        await c.post("/api/settings/kakao-page-login", json={"cookies_json": export()})
        r = await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [4, 1, 2, 5]}); assert r.status_code == 200
        st = await wait_manual(c); log_text = "\n".join(st["log"])
        assert st["status"] == "success", st
        assert sorted(p.name for p in folder.glob("*.zip")) == ["0001_프롤로그.zip", "0002_1화.zip", "0004_3화.zip"], sorted(p.name for p in folder.glob("*"))   # 잠긴 5번은 건너뜀
        assert "받음 3개" in log_text and "잠겨서 건너뜀 1개" in log_text
        with zipfile.ZipFile(folder / "0001_프롤로그.zip") as zf: assert zf.namelist() == ["001.jpg", "002.jpg"]
        assert "<Web>https://page.kakao.com/content/111</Web>" in (folder / "info.xml").read_text(encoding="utf-8") and (folder / "cover.jpg").is_file()   # info.xml/표지도 같이 생긴다
        rows_h, total = repository.list_episode_history(status="success"); assert total == 3 and {r["title_name"] for r in rows_h} == {SERIES}
        await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [1, 2, 4]}); st = await wait_manual(c)
        assert "다시 받아 교체" in "\n".join(st["log"]) and "교체 3개" in "\n".join(st["log"]) and len(list(folder.glob("*.zip"))) == 3   # 이미 받은 회차도 다시 받아 교체(파일이 늘지 않음)
        print("5) 수동 받기 OK (번호순, 지정 폴더에 저장, 잠긴 회차 건너뜀, 이미 받은 회차는 다시 받아 교체, info.xml/표지, 이력 기록)")

        # ── 6. 실패/충돌 ──
        for f in folder.glob("*.zip"): f.unlink()
        S["fail_images"] = True
        await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [1, 2]}); st = await wait_manual(c)
        assert st["status"] == "error" and not list(folder.glob("*.zip")) and not list(folder.glob("*.part"))
        S["fail_images"] = False; S["profile"] = "out"
        await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [1]}); st = await wait_manual(c)
        assert "로그인이 풀려" in "\n".join(st["log"]) and not list(folder.glob("*.zip"))
        S["profile"] = "ok"
        gate = asyncio.Event(); original = kp.download_selected
        async def slow(*a, **k): await gate.wait(); return await original(*a, **k)
        kp.download_selected = slow
        r1 = await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [1]}); r2 = await c.post("/api/kakao-manual/run", json={"series_id": 111, "numbers": [2]})
        assert r1.status_code == 200 and r2.status_code == 409
        gate.set(); await wait_manual(c); kp.download_selected = original
        print("6) 실패/충돌 OK (이미지 실패 시 흔적 없음, 로그인 풀림이면 받지 않음, 동시 실행 409)")

        # ── 7. 백업 ──
        b = repository.export_all(); keys = {x["key"] for x in b["settings"]}
        assert kp.DOWNLOAD_ROOT_SETTING_KEY in keys and "kakao_page_cookies" not in keys and "val-" not in json.dumps(b)
        print("7) 백업 OK (다운로드 폴더는 포함, 쿠키는 제외)")
asyncio.run(main())
print("\n전부 통과")
