"""메타 동기화: 네이버+카카오페이지 모두 info.xml과 커버를 새로 받아 교체한다(예전 카카오웹툰 커버도 카카오페이지 표지로)."""
import asyncio
import tempfile
from pathlib import Path
import httpx

from app import db, repository, tracker, job_status, kakao_page_download as kp, kakao_cover, naver_api
db.get_connection()
import app.main as m
from app.config import get_settings

OLD = b"OLD-COVER"; NEW_KAKAO = b"\xff\xd8\xffNEW-KAKAO"; NEW_NAVER = b"\xff\xd8\xffNEW-NAVER"
LEGACY_XML = '<ComicInfo><Series>옛</Series><Web>https://webtoon.kakao.com/content/옛/123</Web></ComicInfo>'
_o = asyncio.sleep
async def _ns(x): await _o(0)
tracker.asyncio.sleep = _ns

# ── 가짜 카카오페이지 ──
SERIES = {1: {"id": 1, "title": "쌍갑포차", "authors": "배혜수", "description": "설명", "sub_category": "드라마", "age_grade": 15, "on_issue": "Y"},
          2: {"id": 2, "title": "제외작", "authors": "가", "sub_category": "액션", "age_grade": 0, "on_issue": "N"},
          3: {"id": 3, "title": "도굴왕: 엔드라인", "authors": "나", "age_grade": 19, "on_issue": "Y"},
          4: {"id": 4, "title": "폴더없음", "authors": "다"}, 5: {"id": 5, "title": "조회실패", "authors": "라"}, 6: {"id": 6, "title": "표지실패", "authors": "마"}}
ABOUT = {1: {"author_list": [{"name": "배혜수", "role": "writer"}, {"name": "그림가", "role": "illustrator"}, {"name": "원작가", "role": "original_author"}], "theme_keyword_list": [{"title": "귀신"}]}}
calls = []
async def fake_series_item(self, series_id): calls.append(("series", series_id)); return SERIES.get(series_id) if series_id != 5 else None
async def fake_about(self, series_id): return ABOUT.get(series_id)
kp.KakaoPageClient.fetch_series_item = fake_series_item; kp.KakaoPageClient.fetch_about = fake_about
def fake_cover(series_id, **k):
    return None if str(series_id) == "6" else NEW_KAKAO
kakao_cover.fetch_official_cover_bytes = fake_cover

def log_text(): return "\n".join(job_status.snapshot()["metadata_sync"]["log"])

async def main():
    st = get_settings(); kroot = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(kroot))
    for sid, title, status in ((1, "쌍갑포차", "active"), (2, "제외작", "excluded"), (3, "도굴왕: 엔드라인", "unsubscribed"), (4, "폴더없음", "active"), (5, "조회실패", "active"), (6, "표지실패", "active")):
        repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = ? WHERE title_id = ?", (status, sid))
    def folder(name, with_old=True):
        d = kroot / name; d.mkdir(); (d / "info.xml").write_text(LEGACY_XML, encoding="utf-8")
        if with_old: (d / "cover.png").write_bytes(OLD)                                                       # 예전 카카오웹툰에서 받은 커버(확장자도 다름)
        return d
    f1, f2, f3, f5, f6 = folder("쌍갑포차"), folder("제외작"), folder("도굴왕_ 엔드라인"), folder("조회실패"), folder("표지실패")

    # ── 1. 카카오 동기화 ──
    job_status.start("metadata_sync")
    client = kp.KakaoPageClient(None, {}, 10)
    count = await tracker.sync_kakao_metadata(client, st)
    xml1 = (f1 / "info.xml").read_text(encoding="utf-8")
    assert "<Web>https://page.kakao.com/content/1</Web>" in xml1 and "<Writer>배혜수</Writer>" in xml1 and "<CoverArtist>그림가</CoverArtist>" in xml1 and "원작: 원작가" in xml1 and "귀신" in xml1
    assert "<AgeRating>15세 이용가</AgeRating>" in xml1 and (f1 / "cover.jpg").read_bytes() == NEW_KAKAO and not (f1 / "cover.png").exists()   # 커버는 카카오페이지 표지로 교체, 옛 확장자 커버는 지워짐
    assert "<SeriesStatus>완결</SeriesStatus>" in (f2 / "info.xml").read_text(encoding="utf-8") and (f2 / "cover.jpg").read_bytes() == NEW_KAKAO     # 제외됨/완결도 처리
    assert "<AgeRating>18세 이용가</AgeRating>" in (f3 / "info.xml").read_text(encoding="utf-8") and (f3 / "cover.jpg").is_file()                    # 콜론 폴더 규칙(밑줄)으로 찾는다
    assert repository.get_kakao_webtoon(1)["writer_names"] == ["배혜수"]                                                                              # {author}용 글 작가 저장
    assert not (kroot / "폴더없음").exists() and 4 not in [c[1] for c in calls]                                                                        # 폴더가 없으면 건드리지 않는다(카카오에 묻지도 않음)
    assert (f5 / "info.xml").read_text(encoding="utf-8") == LEGACY_XML and (f5 / "cover.png").read_bytes() == OLD                                     # 조회 실패: 기존 파일 그대로
    assert "<Web>https://page.kakao.com/content/6</Web>" in (f6 / "info.xml").read_text(encoding="utf-8") and (f6 / "cover.png").read_bytes() == OLD and not (f6 / "cover.jpg").exists()   # 표지만 못 받으면 info.xml은 갱신, 기존 커버 유지
    t = log_text()
    assert "[카카오] 쌍갑포차 — info.xml 갱신 / 커버 교체" in t and "[카카오] 조회실패 — 작품 정보 조회 실패로 건너뜀" in t and "[카카오] 표지실패 — info.xml 갱신 / 커버는 받지 못해 기존 유지" in t, t
    assert count == 4, count                                                                                                                          # 처리(info.xml 갱신)한 작품: 1·2·3·6
    print("1) 카카오 메타 동기화 OK (info.xml+커버 교체, 옛 커버 정리, 상태 무관, 폴더 없음/조회 실패/표지 실패 격리)")

    # ── 2. 한 작품에서 예외가 나도 나머지는 계속 ──
    orig = kakao_cover.replace_cover
    def boom(folder_, series_id, **k):
        if series_id == 2: raise OSError("디스크 오류")
        return orig(folder_, series_id, **k)
    kakao_cover.replace_cover = boom; (f1 / "cover.jpg").write_bytes(OLD); job_status.start("metadata_sync")
    await tracker.sync_kakao_metadata(client, st); kakao_cover.replace_cover = orig
    assert (f1 / "cover.jpg").read_bytes() == NEW_KAKAO and "[카카오] 제외작 — 처리 중 오류" in log_text()
    print("2) 예외 격리 OK")

    # ── 3. 네이버: 커버도 매번 새로 받아 교체 ──
    naver_root = Path(st.download_root); naver_root.mkdir(parents=True, exist_ok=True)
    repository.upsert_new(title_id="845332", title="네이버작품")
    nf = naver_root / "네이버작품"; nf.mkdir(); (nf / "cover.jpg").write_bytes(OLD); (nf / "cover.png").write_bytes(OLD)
    raw = {"titleName": "네이버작품", "synopsis": "s", "age": {"type": "RATE_ALL"}, "gfpAdCustomParam": {}, "thumbnailUrl": "https://img/cover.jpg", "webtoonLevelCode": "WEBTOON",
           "communityArtists": [{"artistId": 1, "name": "글", "artistTypeList": ["ARTIST_WRITER"]}]}
    async def fake_info(session, title_id, timeout): return naver_api._parse_title_info(raw, title_id)
    naver_api.fetch_title_info = fake_info
    class Resp:
        status = 200
        async def read(self): return NEW_NAVER
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class Sess:
        def get(self, url, **k): return Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    tracker.aiohttp.ClientSession = lambda *a, **k: Sess()
    job_status.start("metadata_sync"); n = await tracker.sync_metadata_for_all(st)
    assert n == 1 and (nf / "cover.jpg").read_bytes() == NEW_NAVER and not (nf / "cover.png").exists()               # 있던 커버도 새로 받아 교체(다른 확장자 커버는 정리)
    assert "<Title>네이버작품</Title>" in (nf / "info.xml").read_text(encoding="utf-8")
    print("3) 네이버 커버 교체 OK")

    # ── 4. API: 카카오웹툰 관리를 켰을 때만 카카오도 함께 ──
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        async def run():
            calls.clear(); job_status.start("metadata_sync")
            assert (await c.post("/api/metadata/sync")).status_code == 200
            for _ in range(200):
                await _o(0.02); s = job_status.snapshot()["metadata_sync"]
                if s["status"] != "running": return s
            raise AssertionError("안 끝남")
        repository.set_setting("kakao_webtoons_enabled", "0"); s = await run()
        assert s["status"] == "success" and calls == [] and "카카오" not in "\n".join(s["log"]).replace("카카오 메타", "")
        repository.set_setting("kakao_webtoons_enabled", "1"); s = await run()
        assert s["status"] == "success" and sorted({c_[1] for c_ in calls}) == [1, 2, 3, 5, 6] and "카카오 메타 동기화 시작" in "\n".join(s["log"])
        orig_sync = tracker.sync_kakao_metadata
        async def fail(client_, settings_): raise RuntimeError("카카오 전체 실패")
        tracker.sync_kakao_metadata = fail; s = await run(); tracker.sync_kakao_metadata = orig_sync
        assert s["status"] == "error" and "카카오 전체 실패" in "\n".join(s["log"])
    print("4) API OK (카카오 관리 켰을 때만, 전체 실패 시 오류 표시)")
asyncio.run(main())
print("\n전부 통과")
