"""카카오 작가: 한 항목에 여러 명이 들어 있어도 한 명씩 나눠 다루고, 이미 묶여 저장된 항목은 정리하고, 원작자 여부를 저장/표시한다."""
import asyncio
import httpx

from app import db, repository, comicinfo, tracker, kakao_page_download as kp
db.get_connection()
import app.main as m
from app.config import get_settings
from app import kakao_authors

# ── 1. 이름 나누기: "연상호, 민홍남, 황은영"이 한 항목의 이름으로 와도 세 명 ──
about = {"author_list": [{"name": "글쓴이", "role": "writer"}, {"name": "그림쓴이", "role": "illustrator"},
                         {"name": "연상호, 민홍남, 황은영", "role": "original_author"}]}
assert kakao_authors.split_authors(about) == (["글쓴이"], ["그림쓴이"], ["연상호", "민홍남", "황은영"])
assert kakao_authors.authors_to_register(about) == ["연상호", "민홍남", "황은영"]
about2 = {"author_list": [{"name": "글A，글B", "role": "writer"}, {"name": "글B", "role": "writer"}, {"name": "  글C  ,, ", "role": "writer"}, {"name": "", "role": "writer"}]}
assert kakao_authors.split_authors(about2)[0] == ["글A", "글B", "글C"]                                               # 전각 쉼표, 중복, 공백, 빈 값
xml = comicinfo.build_kakao_comicinfo_xml({"title": "t"}, 1, about)
assert "<Writer>글쓴이</Writer>" in xml and "원작: 연상호, 민홍남, 황은영" in xml
assert kakao_authors.split_authors(None) == ([], [], []) and kakao_authors.authors_to_register({}) == []
print("1) 이름 나누기 OK")

# ── 2. 이미 묶여서 저장된 카카오 작가 정리(시작할 때) ──
repository.upsert_watched_author("연상호, 민홍남, 황은영", "연상호, 민홍남, 황은영", False, "kakao")            # 제외된 작품에서 꺼진 채 등록된 것
repository.upsert_watched_author("오오히라 요우, 피이", "오오히라 요우, 피이", True, "kakao")                   # 켜져 있던 것
repository.upsert_watched_author("피이", "피이", False, "kakao")                                                 # 이미 따로 있고 꺼 둔 것
repository.upsert_watched_author("9001", "네이버작가", True, "naver")
fixed = db.fix_combined_kakao_authors()
got = {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}
assert got == {"연상호": False, "민홍남": False, "황은영": False, "오오히라 요우": True, "피이": False}, got      # 나뉘고, 켜짐 상태를 이어받고, 이미 있던 "피이"는 그대로
assert fixed == 2 and [a.author_id for a in repository.list_watched_authors("naver")] == ["9001"]              # 네이버는 건드리지 않는다
assert db.fix_combined_kakao_authors() == 0                                                                   # 멱등
print("2) 묶인 작가 정리 OK")

# ── 3. 원작자 여부 저장/표시 ──
repository.upsert_new_kakao_webtoon(71000001, "다작가작품", status=repository.STATUS_ACTIVE)
repository.set_kakao_authors(71000001, ["글쓴이"], ["연상호", "민홍남"])
w = repository.get_kakao_webtoon(71000001); assert w["writer_names"] == ["글쓴이"] and w["origin_names"] == ["연상호", "민홍남"]
repository.set_kakao_authors(71000001, [], [])                                                                # 정보를 못 받은 날에는 기존 값을 지우지 않는다
assert repository.get_kakao_webtoon(71000001)["origin_names"] == ["연상호", "민홍남"]
repository.set_kakao_authors(71000001, ["글쓴이"], [])                                                          # 원작자가 없어진 것이 확인되면 지운다(글 작가는 있음)
assert repository.get_kakao_webtoon(71000001)["origin_names"] == []
repository.set_kakao_authors(71000001, ["글쓴이"], ["연상호", "민홍남"])
assert repository.list_kakao_origin_names() == {"연상호", "민홍남"}

async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        rows = {r["author_id"]: r for r in (await c.get("/api/kakao/watched-authors")).json()}
        assert rows["연상호"]["is_origin"] is True and rows["피이"]["is_origin"] is False
        assert (await c.get("/api/kakao/origin-authors")).json() == ["민홍남", "연상호"]
        # 구독하면 원작자 이름이 저장되고 관심 작가로도 등록된다
        async def fake_about(self, series_id): return {"author_list": [{"name": "글쓴이2", "role": "writer"}, {"name": "원작A, 원작B", "role": "original_author"}]}
        kp.KakaoPageClient.fetch_about = fake_about
        r = await c.post("/api/kakao-webtoons/71000002/subscribe", json={"title": "구독작", "thumbnail_url": "u", "author_summary": "글쓴이2,원작A,원작B"}); assert r.status_code == 200
        assert repository.get_kakao_webtoon(71000002)["origin_names"] == ["원작A", "원작B"]
        assert {"원작A", "원작B"} <= {a.author_id for a in repository.list_watched_authors("kakao")} and "원작A, 원작B" not in {a.author_id for a in repository.list_watched_authors("kakao")}
asyncio.run(api())
print("3) 원작자 저장/표시 API OK")

# ── 4. 전체 재동기화/메타 동기화도 원작자를 저장한다 ──
async def resync():
    from app import job_status
    job_status.start("registry")
    repository.upsert_new_kakao_webtoon(71000003, "재동기화작", status=repository.STATUS_ACTIVE)
    await tracker.resync_kakao_registry(kp.KakaoPageClient(None, {}, 5), get_settings())
    assert repository.get_kakao_webtoon(71000003)["origin_names"] == ["원작A", "원작B"] and repository.get_kakao_webtoon(71000003)["writer_names"] == ["글쓴이2"]
asyncio.run(resync())
print("4) 재동기화 저장 OK")
print("\n전부 통과")
