"""지금 전체 재동기화 — 카카오: 작품 정보(글/그림/원작)로 관심 작가 등록 + 글 작가 저장, 상태별 규칙, 실패 격리, 네이버와 함께 실행."""
import asyncio
import httpx
from app import db, repository, tracker, job_status, kakao_page_download as kp
db.get_connection()
import app.main as m
from app.config import get_settings

ABOUT = {
    1: {"author_list": [{"name": "글1", "role": "writer"}, {"name": "그림1", "role": "illustrator"}, {"name": "원작1", "role": "original_author"}]},   # 원작자 있음
    2: {"author_list": [{"name": "글2", "role": "writer"}, {"name": "그림2", "role": "illustrator"}]},                                                 # 원작자 없음
    3: {"author_list": [{"name": "제외작가", "role": "writer"}]},                                                                                        # 제외됨
    4: {"author_list": [{"name": "글4", "role": "writer"}]},                                                                                            # 목록(미등록)
    6: {"author_list": [{"name": "꺼둔작가", "role": "writer"}]},
}
calls = []
async def fake_about(self, series_id):
    calls.append(series_id)
    if series_id == 5: raise RuntimeError("network")                                                                                                  # 예외도 이 작품만 건너뛴다
    return ABOUT.get(series_id)                                                                                                                       # 6은 정보 있음, 7은 None(못 받음)
kp.KakaoPageClient.fetch_about = fake_about
_o = asyncio.sleep
async def _ns(x): await _o(0)
tracker.asyncio.sleep = _ns

def setup_rows():
    for sid, title, status in ((1, "원작있는작품", "active"), (2, "글만있는작품", "unsubscribed"), (3, "제외된작품", "excluded"), (4, "목록작품", "unregistered"),
                               (5, "예외작품", "active"), (6, "꺼둔작가작품", "active"), (7, "정보없는작품", "active")):
        repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = ? WHERE title_id = ?", (status, sid))
def watched(): return {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}
def log_text(): return "\n".join(job_status.snapshot()["registry"]["log"])

async def main():
    setup_rows(); settings = get_settings()
    repository.upsert_watched_author("꺼둔작가", "꺼둔작가", enabled=False, platform="kakao")                          # 사용자가 꺼 둔 작가
    repository.set_setting("auto_register_author_on_subscribe", "0")                                                # 구독 시 자동 등록 설정이 꺼져 있어도, 재동기화는 사용자가 직접 누른 것이라 등록한다
    job_status.start("registry")

    # ── 1. 카카오 재동기화 ──
    client = kp.KakaoPageClient(None, {}, 10)
    count = await tracker.resync_kakao_registry(client, settings)
    w = watched()
    assert w == {"원작1": True, "글2": True, "제외작가": False, "글4": True, "꺼둔작가": False}, w                    # 원작자 우선, 없으면 글 / 제외됨은 "전체 작가 목록"(꺼짐)으로만 / 꺼 둔 것은 그대로 / 그림 작가는 없음
    assert count == 4, count                                                                                          # 제외되지 않은 채로 작가 등록이 처리된 작품 수(1·2·4·6 — 6은 사용자가 꺼 둔 작가라 켜지진 않지만 처리는 됨)
    g = repository.get_kakao_webtoon
    assert g(1)["writer_names"] == ["글1"] and g(2)["writer_names"] == ["글2"] and g(3)["writer_names"] == ["제외작가"] and g(7)["writer_names"] == []     # {author}용 글 작가 저장, 정보 없으면 그대로
    assert sorted(calls) == [1, 2, 3, 4, 5, 6, 7]                                                                     # 상태와 무관하게 모두 훑는다
    t = log_text()
    assert "[카카오] 원작있는작품 — 원작자 등록: 원작1" in t and "[카카오] 글만있는작품 — 작가 등록: 글2" in t and "[카카오] 제외된작품 — 작가 등록(전체 작가 목록만): 제외작가" in t
    assert "[카카오] 예외작품 — 작품 정보 조회 실패" in t and "[카카오] 정보없는작품 — 작품 정보 조회 실패" in t and "[카카오] 꺼둔작가작품 — 작가 등록: 꺼둔작가" in t
    print("1) 카카오 재동기화 OK (원작자 우선, 상태별 규칙, 꺼 둔 작가 유지, 글 작가 저장, 실패 격리, 로그)")

    # ── 2. 다시 눌러도 같은 결과(멱등) ──
    before = watched(); await tracker.resync_kakao_registry(client, settings); assert watched() == before
    print("2) 재실행 OK (멱등)")

    # ── 3. 전체 재동기화 API: 카카오 관리를 켰을 때만 카카오를 함께 ──
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        async def run_resync():
            calls.clear(); job_status.start("registry")
            assert (await c.post("/api/registry/resync")).status_code == 200
            for _ in range(200):
                await _o(0.02)
                st = job_status.snapshot()["registry"]
                if st["status"] != "running": return st
            raise AssertionError("재동기화가 안 끝남")
        with db.write_transaction() as cx: cx.execute("DELETE FROM watched_authors WHERE platform = 'kakao'")
        repository.set_setting("kakao_webtoons_enabled", "0"); st = await run_resync()
        assert calls == [] and watched() == {} and st["status"] == "success"                                            # 꺼져 있으면 카카오는 건드리지 않는다
        repository.set_setting("kakao_webtoons_enabled", "1"); st = await run_resync()
        assert st["status"] == "success" and "원작1" in watched() and "글2" in watched() and sorted(calls) == [1, 2, 3, 4, 5, 6, 7]
        text = "\n".join(st["log"]); assert "카카오 작가 재동기화 시작" in text and "완료" in text, text[-300:]
        print("3) 전체 재동기화 API OK (카카오 관리 켰을 때만 함께, 로그/상태)")

        # ── 4. 카카오 쪽이 통째로 실패해도 네이버 재동기화 결과는 남고 상태는 오류로 ──
        async def boom(client_, settings_): raise RuntimeError("카카오 전체 실패")
        orig = tracker.resync_kakao_registry; tracker.resync_kakao_registry = boom
        st = await run_resync(); tracker.resync_kakao_registry = orig
        assert st["status"] == "error" and "카카오 전체 실패" in "\n".join(st["log"])
        print("4) 카카오 전체 실패 OK (오류로 표시, 다른 처리 중단 안 함)")
asyncio.run(main())
print("\n전부 통과")
