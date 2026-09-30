"""원작/글 작가가 여러 명일 때: 관심 작가는 한 명씩 따로 등록되고, info.xml/{author}에도 전부 들어간다(카카오 + 네이버)."""
import asyncio
import httpx
from app import db, repository, tracker, naver_api, kakao_page_download as kp, comicinfo
db.get_connection()
import app.main as m

def names(platform): return {a.author_id: a.enabled for a in repository.list_watched_authors(platform)}

# ── 카카오: 원작 3명(연상호, 민홍남, 황은영) ──
about3 = {"author_list": [{"name": "글쓴이", "role": "writer"}, {"name": "그림쓴이", "role": "illustrator"},
                          {"name": "연상호", "role": "original_author"}, {"name": "민홍남", "role": "original_author"}, {"name": "황은영", "role": "original_author"}]}
assert kp.authors_to_register(about3) == ["연상호", "민홍남", "황은영"]                              # 원작자가 있으면 원작자 전원(글 작가는 제외), 순서 유지
assert kp.split_authors(about3) == (["글쓴이"], ["그림쓴이"], ["연상호", "민홍남", "황은영"])
# 글 작가가 여러 명이고 원작이 없으면 글 작가 전원
about_w = {"author_list": [{"name": "글A", "role": "writer"}, {"name": "글B", "role": "writer"}, {"name": "글A", "role": "writer"}, {"name": "그림", "role": "illustrator"}]}
assert kp.authors_to_register(about_w) == ["글A", "글B"]                                              # 같은 이름은 한 번만
xml = comicinfo.build_kakao_comicinfo_xml({"title": "t", "authors": ""}, 1, about3)
assert "<Writer>글쓴이</Writer>" in xml and "원작: 연상호, 민홍남, 황은영" in xml                        # info.xml에는 원작자 전원
assert "<Writer>글A, 글B</Writer>" in comicinfo.build_kakao_comicinfo_xml({"title": "t"}, 1, about_w)

async def kakao_subscribe():
    async def fake_about(self, series_id): return about3
    kp.KakaoPageClient.fetch_about = fake_about
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        r = await c.post("/api/kakao-webtoons/71000001/subscribe", json={"title": "다작가작품", "thumbnail_url": "u", "author_summary": "글쓴이,그림쓴이"})
        assert r.status_code == 200
    assert names("kakao") == {"연상호": True, "민홍남": True, "황은영": True}, names("kakao")            # 구독하면 각각 따로 등록
    assert repository.get_kakao_webtoon(71000001)["writer_names"] == ["글쓴이"]                          # 글 작가는 {author}용으로 저장
asyncio.run(kakao_subscribe())

# ── 네이버: 원작자 3명 / 글 작가 2명 ──
def raw(*artists):
    return {"titleName": "작품", "synopsis": "s", "age": {"type": "RATE_ALL"}, "gfpAdCustomParam": {}, "thumbnailUrl": "u", "webtoonLevelCode": "WEBTOON",
            "communityArtists": [{"artistId": i, "name": n, "artistTypeList": t} for i, n, t in artists]}
O = lambda i, n: (i, n, ["ARTIST_NOVEL_ORIGIN"]); W = lambda i, n: (i, n, ["ARTIST_WRITER"])
info = naver_api._parse_title_info(raw(W(1, "글1"), W(2, "글2"), O(10, "원작1"), O(11, "원작2"), O(12, "원작3")), "500")
assert tracker.authors_to_register(info) == [("10", "원작1"), ("11", "원작2"), ("12", "원작3")]
info_w = naver_api._parse_title_info(raw(W(1, "글1"), W(2, "글2")), "501")
assert tracker.authors_to_register(info_w) == [("1", "글1"), ("2", "글2")]
async def fake_fetch(session, title_id, timeout): return {"500": info, "501": info_w}[title_id]
naver_api.fetch_title_info = fake_fetch
async def naver_enrich():
    from app.config import get_settings
    for tid in ("500", "501"): repository.upsert_new(title_id=tid, title=f"작품{tid}")
    await tracker.enrich_one(None, "500", get_settings(), register_authors_enabled=True)
    await tracker.enrich_one(None, "501", get_settings(), register_authors_enabled=True)
    assert names("naver") == {"10": True, "11": True, "12": True, "1": True, "2": True}, names("naver")   # 각각 따로
asyncio.run(naver_enrich())
print("여러 작가 등록 OK (카카오/네이버 모두 한 명씩 따로, 원작자 우선, 중복 제거)")
print("\n전부 통과")
