"""원작자 — 네이버: 등록 대상 선택(원작자 우선), 후보/표시, 신작 발견 매칭 / 카카오: 구독 시 작가 등록."""
import asyncio
import json
import httpx

from app import db, repository, naver_api, tracker
db.get_connection()
import app.main as m
from app.config import get_settings

def raw(*artists):
    return {"titleName": "작품", "synopsis": "s", "age": {"type": "RATE_ALL"}, "gfpAdCustomParam": {}, "thumbnailUrl": "u", "webtoonLevelCode": "WEBTOON",
            "communityArtists": [{"artistId": i, "name": n, "artistTypeList": t} for i, n, t in artists]}
W = (1, "글작가", ["ARTIST_WRITER"]); P = (2, "그림작가", ["ARTIST_PAINTER"]); O = (3, "원작자", ["ARTIST_NOVEL_ORIGIN"])

# ── 1. 파싱과 등록 대상 ──
both = naver_api._parse_title_info(raw(W, P, O), "100")
assert both.origin_id_name_pairs == [("3", "원작자")] and both.writer_id_name_pairs == [("1", "글작가")] and both.novel_origin_names == ["원작자"]
assert tracker.authors_to_register(both) == [("3", "원작자")]                                   # 작가와 원작자가 둘 다 있으면 원작자
only_writer = naver_api._parse_title_info(raw(W, P), "101")
assert only_writer.origin_id_name_pairs == [] and tracker.authors_to_register(only_writer) == [("1", "글작가")]      # 원작자가 없으면 작가
only_origin = naver_api._parse_title_info(raw(P, O), "102")
assert tracker.authors_to_register(only_origin) == [("3", "원작자")]                             # 원작자만 있어도 원작자
same_person = naver_api._parse_title_info(raw((1, "겸업", ["ARTIST_WRITER", "ARTIST_NOVEL_ORIGIN"])), "103")
assert tracker.authors_to_register(same_person) == [("1", "겸업")]
print("1) 파싱/등록 대상 OK (원작자 우선, 없으면 작가)")

# ── 2. 구독 시 등록 (enrich_one) ──
infos = {"100": both, "101": only_writer}
async def fake_fetch(session, title_id, timeout): return infos[title_id]
naver_api.fetch_title_info = fake_fetch
async def t2():
    for tid in ("100", "101"): repository.upsert_new(title_id=tid, title=f"작품{tid}")
    ok, msg = await tracker.enrich_one(None, "100", get_settings(), register_authors_enabled=True)
    assert ok and msg == "원작자 등록: 원작자", msg
    assert {a.author_id: a.enabled for a in repository.list_watched_authors()} == {"3": True}                     # 원작자만 등록, 글작가는 등록 안 됨
    ok, msg = await tracker.enrich_one(None, "101", get_settings(), register_authors_enabled=False)
    assert msg == "작가 등록: 글작가" and {a.author_id: a.enabled for a in repository.list_watched_authors()} == {"3": True, "1": False}   # 원작자 없는 작품은 작가, 끈 채로 등록
    row = repository.fetchone("SELECT writer_ids, writer_names, origin_ids, origin_names FROM webtoons WHERE title_id = '100'")
    assert json.loads(row["writer_names"]) == ["글작가"] and json.loads(row["origin_ids"]) == ["3"] and json.loads(row["origin_names"]) == ["원작자"]   # 카드에 보이는 작가 정보는 그대로
    row = repository.fetchone("SELECT origin_ids FROM webtoons WHERE title_id = '101'"); assert json.loads(row["origin_ids"]) == []
    print("2) 구독 시 등록 OK (원작자 있으면 원작자만, 없으면 작가, 작가 표시 정보는 유지)")
asyncio.run(t2())

# ── 3. 후보 목록/표시 ──
pairs = repository.list_all_writer_id_name_pairs()
assert pairs == {"1": "글작가", "3": "원작자"} and repository.list_origin_author_ids() == {"3"}

# ── 4. 신작 발견 매칭 ──
match = tracker._matches_enabled_writer
assert match({"author": {"writers": [{"id": 1}]}}, {"1"}) and not match({"author": {"writers": [{"id": 1}]}}, {"9"})
assert match({"author": {"writers": [{"id": 1}], "originAuthors": [{"id": 3}]}}, {"3"})                             # 원작자 필드 이름에 기대지 않고 찾는다
assert not match({"author": {"name": "x", "count": 3}}, {"3"}) and not match({}, {"3"}) and not match({"author": None}, {"3"})
print("3) 후보/신작 발견 매칭 OK")

# ── 5. API 표시 + 카카오 구독 시 등록 + 백업 ──
async def t5():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = {a["author_id"]: a for a in (await c.get("/api/authors/interested")).json()}
        assert r["3"]["is_origin"] is True and r["3"]["enabled"] is True and r["1"]["is_origin"] is False and r["1"]["enabled"] is False
        # 카카오: 구독하면 정보 탭의 원작자(없으면 글 작가)를 관심 작가로 등록 — 뷰어 서버 설정과 무관
        from app import kakao_page_download as kpd
        about = {"author_list": [{"name": "글쓴이", "role": "writer"}, {"name": "그린이", "role": "illustrator"}, {"name": "원작가", "role": "original_author"}]}
        async def fake_about(self, series_id): return about
        kpd.KakaoPageClient.fetch_about = fake_about
        body = {"title": "카카오작품", "thumbnail_url": "u", "author_summary": "글쓴이, 그린이, 원작가"}
        assert (await c.post("/api/kakao-webtoons/71000001/subscribe", json=body)).status_code == 200
        assert {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")} == {"원작가": True}        # 원작자만
        about["author_list"] = [x for x in about["author_list"] if x["role"] != "original_author"]
        await c.post("/api/kakao-webtoons/71000003/subscribe", json={**body, "title": "원작 없는 작품"})
        assert {a.author_id for a in repository.list_watched_authors("kakao")} == {"원작가", "글쓴이"}                 # 원작자가 없으면 글 작가(그림 작가는 등록 안 됨)
        repository.set_watched_author_enabled("원작가", False, "원작가", "kakao")                                      # 사용자가 끈 것은 다시 켜지 않는다
        await c.post("/api/kakao-webtoons/71000001/subscribe", json=body)
        assert {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}["원작가"] is False
        async def no_about(self, series_id): return None
        kpd.KakaoPageClient.fetch_about = no_about                                                                       # 정보 탭을 못 받으면 등록하지 않는다
        await c.post("/api/kakao-webtoons/71000004/subscribe", json={**body, "title": "정보 없음"})
        assert {a.author_id for a in repository.list_watched_authors("kakao")} == {"원작가", "글쓴이"}
        kpd.KakaoPageClient.fetch_about = fake_about
        repository.set_setting("auto_register_author_on_subscribe", "0")                                               # 설정을 끄면 등록 안 함
        await c.post("/api/kakao-webtoons/71000002/subscribe", json={"title": "다른작품", "thumbnail_url": "u", "author_summary": "새작가"})
        assert {a.author_id for a in repository.list_watched_authors("kakao")} == {"원작가", "글쓴이"}
        repository.set_setting("auto_register_author_on_subscribe", None)
        b = repository.export_all(); w = [x for x in b["webtoons"] if x["title_id"] == "100"][0]
        assert json.loads(w["origin_ids"]) == ["3"]
        repository.restore_all(b); assert repository.list_origin_author_ids() == {"3"}                                   # 백업/복원에도 유지
    print("4) API/카카오 구독 등록/백업 OK")
asyncio.run(t5())
print("\n전부 통과")
