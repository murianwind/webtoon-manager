"""카카오 목록 캐시 — 화면은 캐시만 읽고, 목록은 백그라운드로 채워지고, 갱신이 굳지 않는다."""
import asyncio
import json
import time
import httpx

from app import db, repository, kakao_api, kakao_catalog as kc, scheduler as sch
db.get_connection()
import app.main as m

def item(i, title, **k):
    return {"title_id": i, "title_name": title, "is_adult": False, "author_names": k.get("authors", ["작가"]), "has_update": k.get("up", False),
            "is_new": False, "is_paused": False, "is_finished": False, "thumbnail_url": f"https://page-images/{i}"}
CAT = {"items": [item(71000001, "가"), item(71000002, "나"), item(71000003, "다", up=True)], "ok": True, "calls": 0, "gate": None, "boom": False}
async def fake_checked(session, timeout):
    CAT["calls"] += 1
    if CAT["gate"] is not None: await CAT["gate"].wait()
    if CAT["boom"]: raise RuntimeError("network")
    return list(CAT["items"]), CAT["ok"]
kakao_api.fetch_weekday_catalog_checked = fake_checked
class FakeSess:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
kc.aiohttp.ClientSession = lambda *a, **k: FakeSess()
def reset_memory(): kc._state.update(loaded=False, items=None, fetched_at=None)
async def settle():
    for _ in range(200):
        if not kc.is_refreshing() and not kc._tasks: return
        await asyncio.sleep(0.01)
    raise AssertionError("갱신이 안 끝남")

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.upsert_new_kakao_webtoon(71000002, "나", status=repository.STATUS_ACTIVE)
    repository.upsert_new_kakao_webtoon(999, "장기휴재로 빠진 구독작", status=repository.STATUS_ACTIVE)       # 목록에 없는 구독 이력 작품(DB 보완)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 1. 처음(캐시 없음): 기다리지 않고 바로 응답, 백그라운드로 채움 ──
        t0 = time.monotonic(); r = (await c.get("/api/kakao-list")).json()
        assert time.monotonic() - t0 < 1 and r["refreshing"] is True and r["refreshed_at"] is None
        assert [w["title"] for w in r["items"]] == ["장기휴재로 빠진 구독작", "나"] or {w["title"] for w in r["items"]} == {"장기휴재로 빠진 구독작"}   # 목록이 비어도 DB 기록은 보인다
        await settle()
        r = (await c.get("/api/kakao-list")).json()
        assert r["refreshing"] is False and r["refreshed_at"] and CAT["calls"] == 1
        assert sorted(w["title"] for w in r["items"]) == ["가", "나", "다", "장기휴재로 빠진 구독작"]
        assert {w["title"]: w["status"] for w in r["items"]}["나"] == "active" and {w["title"]: w["has_new_episode"] for w in r["items"]}["다"] is True
        v1 = r["version"]
        for _ in range(3): await c.get("/api/kakao-list")
        await settle(); assert CAT["calls"] == 1                                            # 계속 열어도 카카오를 다시 부르지 않는다
        assert (await c.get("/api/kakao-list")).json()["version"] == v1                     # 바뀐 게 없으면 version도 같다
        print("1) 처음 열기 OK (기다리지 않음, 백그라운드로 채움, 이후 캐시만 사용, 변화 없으면 version 동일)")

        # ── 2. 상태가 바뀌면 version이 바뀌고, 제외된 건 빠진다 ──
        repository.upsert_new_kakao_webtoon(71000001, "가", status=repository.STATUS_ACTIVE)
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = 'excluded' WHERE title_id = 71000001")
        r = (await c.get("/api/kakao-list")).json()
        assert "가" not in [w["title"] for w in r["items"]] and r["version"] != v1
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = 'unregistered' WHERE title_id = 71000001")
        print("2) 상태 변경 반영 OK (version 변경, 제외됨 숨김)")

        # ── 3. 재시작: 저장된 캐시를 바로 보여주고, 시작 때 새로 채운다 ──
        reset_memory(); calls = CAT["calls"]; CAT["items"] = [item(71000001, "가"), item(71000002, "나"), item(71000003, "다", up=True), item(71000004, "새작품")]
        items, at = kc.snapshot()
        assert len(items) == 3 and at                                                         # 재시작 직후에도 (예전) 목록이 바로 있다
        assert kc.start_refresh() is True; await settle()                                     # main.py가 시작할 때 부르는 것
        assert CAT["calls"] == calls + 1 and len(kc.snapshot()[0]) == 4                       # 새로 채워짐
        repository.set_setting(kc.SETTING_KEY, json.dumps({"v": 0, "fetched_at": time.time(), "items": [item(1, "옛 형식")]})); reset_memory()
        assert kc.snapshot() == ([], None)                                                    # 저장 형식이 다른 캐시는 버린다
        repository.set_setting(kc.SETTING_KEY, "깨진 값"); reset_memory(); assert kc.snapshot() == ([], None)
        print("3) 재시작 OK (저장된 캐시 즉시 사용, 시작 때 새로 채움, 다른 형식/깨진 캐시는 버림)")

        # ── 4. 일부만 받았거나 실패하면 멀쩡한 캐시를 덮어쓰지 않는다 ──
        reset_memory(); CAT["items"] = [item(71000001, "가"), item(71000002, "나")]; await kc.refresh()
        good = kc.snapshot()
        CAT["items"], CAT["ok"] = [item(71000001, "가")], False
        assert await kc.refresh() is False and kc.snapshot()[0] == good[0]                    # 일부 요일 실패 → 예전 캐시 유지
        CAT["boom"] = True; assert await kc.refresh() is False and kc.snapshot()[0] == good[0]; CAT["boom"] = False
        reset_memory(); repository.set_setting(kc.SETTING_KEY, None); reset_memory()
        assert await kc.refresh() is True and len(kc.snapshot()[0]) == 1 and kc.snapshot()[1] == 0.0          # 캐시가 아예 없을 땐 불완전해도 임시로 쓰되
        calls = CAT["calls"]; CAT["ok"] = True; CAT["items"] = [item(71000001, "가"), item(71000002, "나")]
        assert kc.ensure_fresh() is True; await settle() and None; assert CAT["calls"] == calls + 1 and len(kc.snapshot()[0]) == 2   # 곧바로 다시 채운다
        print("4) 불완전한 갱신 OK (예전 캐시 유지, 캐시 없을 땐 임시 사용 후 재시도)")

        # ── 5. 오래된 캐시는 백그라운드로 다시 채우되, 화면은 기다리지 않는다 ──
        _, at = kc.snapshot(); kc._state["fetched_at"] = time.time() - (kc.REFRESH_INTERVAL_MINUTES * 60 + 3600)
        calls = CAT["calls"]; CAT["gate"] = asyncio.Event()
        t0 = time.monotonic(); r = (await c.get("/api/kakao-list")).json()
        assert time.monotonic() - t0 < 1 and r["refreshing"] is True and len(r["items"]) >= 2   # 옛 캐시를 바로 보여주면서 채우는 중
        await c.get("/api/kakao-list", params={"refresh": "true"}); assert CAT["calls"] == calls + 1      # 갱신 중에 또 눌러도 한 번만(단일 실행)
        CAT["gate"].set(); await settle(); CAT["gate"] = None
        assert kc.snapshot()[1] > time.time() - 60 and (await c.get("/api/kakao-list")).json()["refreshing"] is False
        calls = CAT["calls"]; await c.get("/api/kakao-list", params={"refresh": "true"}); await settle(); assert CAT["calls"] == calls + 1    # 새로고침 버튼 = 백그라운드 갱신
        print("5) 오래된 캐시/새로고침 OK (기다리지 않음, 중복 실행 없음)")

        # ── 6. 제외됨/구독해제 탭은 카카오를 부르지 않는다 ──
        repository.upsert_new_kakao_webtoon(71000002, "나", status=repository.STATUS_EXCLUDED)
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = 'excluded' WHERE title_id = 71000002")
        calls = CAT["calls"]; kakao_api.fetch_weekday_catalog = None
        rows = (await c.get("/api/kakao-webtoons", params={"status": "excluded"})).json(); await settle()
        assert [r["title"] for r in rows] == ["나"] and CAT["calls"] == calls and rows[0]["has_new_episode"] is False
        CAT["items"] = [item(71000002, "나", up=True)]; await kc.refresh()
        assert (await c.get("/api/kakao-webtoons", params={"status": "excluded"})).json()[0]["has_new_episode"] is True     # 캐시가 갱신되면 UP 배지도 붙는다
        print("6) 제외됨 탭 OK (카카오를 안 부르고 캐시로 UP 배지 표시)")

        # ── 7. 백업 제외 + 스케줄러 ──
        assert "kakao_catalog_snapshot" not in {x["key"] for x in repository.export_all()["settings"]}
        s = sch.create_scheduler(); job = s.get_job("kakao_catalog_refresh")
        assert job is not None and job.trigger.interval.total_seconds() == 180 * 60
        sch.reschedule_all(s); assert s.get_job("kakao_catalog_refresh") is not None                       # 설정 저장/재등록해도 유지
        calls = CAT["calls"]; repository.set_setting("kakao_webtoons_enabled", "0"); await sch.run_kakao_catalog_refresh_job(); assert CAT["calls"] == calls
        repository.set_setting("kakao_webtoons_enabled", "1"); await sch.run_kakao_catalog_refresh_job(); assert CAT["calls"] == calls + 1
        print("7) 백업 제외/3시간 스케줄 OK (카카오 기능이 꺼져 있으면 안 돎)")
asyncio.run(main())
print("\n전부 통과")
