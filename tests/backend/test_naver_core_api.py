"""네이버 API 모듈(naver_api.py) 특성화 테스트 — 응답 파싱, 재시도/백오프, 페이지 합치기, 오류 처리의 "현재 동작"을 고정한다(뒤쪽 페이지 실패는 오류로 바꿨다).

가짜 응답은 코드가 문서로 남겨 둔 실제 응답 모양(HAR 캡처 기반)을 따른다. 네이버가 응답 형식을 바꿨는지는 잡지 못한다(그건 실서버에서만 확인된다).
"""
import asyncio

from app import naver_api
from app.constants import (
    EPISODE_LIST_MAX_RETRIES, NAVER_ARTIST_OTHER_TITLES_URL, NAVER_CURATION_LIST_URL, NAVER_CURATION_META_URL, NAVER_INFO_URL, NAVER_LIST_URL,
    NAVER_SEARCH_ALL_URL, NAVER_TAG_SHORTCUT_URL, NAVER_WEEKDAY_LIST_URL, RETRY_BACKOFF_BASE_SECONDS,
)
from app.models import EpisodeInfo, NaverListItem
from fake_naver_http import FakeResponse, FakeSession

sleeps = []
async def fake_sleep(seconds): sleeps.append(seconds)
naver_api.asyncio.sleep = fake_sleep

RAW_TITLE = {
    "titleName": "테스트 작품", "synopsis": "줄거리", "thumbnailUrl": "https://img/t.jpg", "webtoonLevelCode": "BEST_CHALLENGE",
    "finished": True, "rest": True, "new": True, "age": {"type": "RATE_18", "description": "18세 이용가"},
    "communityArtists": [
        {"artistId": 1, "name": "글작가", "artistTypeList": ["ARTIST_WRITER", "ARTIST_PAINTER"]},
        {"artistId": 2, "name": "그림작가", "artistTypeList": ["ARTIST_PAINTER"]},
        {"artistId": 3, "name": "원작자", "artistTypeList": ["ARTIST_NOVEL_ORIGIN"]},
        {"artistId": None, "name": "아이디 없음", "artistTypeList": ["ARTIST_WRITER"]},
        {"artistId": 4, "name": "", "artistTypeList": ["ARTIST_WRITER", "ARTIST_PAINTER"]},
    ],
    "gfpAdCustomParam": {"genreTypes": ["FANTASY", "NEW_GENRE"], "tags": ["회귀", "먼치킨"]},
}


def parse_title_info():
    # Scenario 1: 작품 정보 파싱 — 작가는 communityArtists의 역할로 나뉘고, 이름 없는 작가는 이름 목록에서 빠지며 id는 남는다
    info = naver_api._parse_title_info(RAW_TITLE, "777")
    assert (info.title_id, info.title_name, info.synopsis, info.thumbnail_url) == ("777", "테스트 작품", "줄거리", "https://img/t.jpg")
    assert info.is_adult is True and info.is_finished is True and info.is_paused is True and info.is_new is True
    assert info.webtoon_type == "bestChallenge" and info.age_description == "18세 이용가"
    assert info.writer_names == ["글작가"] and info.writer_ids == {"1", "4"} and info.writer_id_name_pairs == [("1", "글작가"), ("4", "")]
    assert info.painter_names == ["글작가", "그림작가"]                       # 이름이 비어 있는 그림작가(4번)는 뺀다
    assert info.novel_origin_names == ["원작자"] and info.origin_id_name_pairs == [("3", "원작자")]
    assert info.genres == ["FANTASY", "NEW_GENRE"] and info.genres_ko == ["판타지", "NEW_GENRE"]   # 모르는 장르 코드는 코드 그대로
    assert info.tags == ["회귀", "먼치킨"]
    # Scenario 2: 비어 있는 응답은 안전한 기본값 — 모르는 작품 종류 코드는 일반 웹툰
    empty = naver_api._parse_title_info({}, "1")
    assert (empty.title_name, empty.is_adult, empty.is_finished, empty.webtoon_type, empty.writer_names, empty.genres_ko, empty.tags) == ("", False, False, "webtoon", [], [], [])
    assert naver_api._parse_title_info({"webtoonLevelCode": "CHALLENGE"}, "1").webtoon_type == "challenge"
    assert naver_api._parse_title_info({"webtoonLevelCode": "???"}, "1").webtoon_type == "webtoon"
    print("1~2) 작품 정보 파싱 OK")


async def title_info_fetch():
    # Scenario 3: 200이면 파싱한 정보, 200이 아니거나 예외면 None(호출자가 건너뜀)
    ok = FakeSession(lambda url, params, n: FakeResponse(200, json_data=RAW_TITLE))
    info = await naver_api.fetch_title_info(ok, "777", 10)
    assert info.title_name == "테스트 작품" and ok.calls[0][0] == NAVER_INFO_URL and ok.calls[0][1] == {"titleId": "777"}
    assert await naver_api.fetch_title_info(FakeSession(lambda *a: FakeResponse(404)), "1", 10) is None
    assert await naver_api.fetch_title_info(FakeSession(lambda *a: RuntimeError("네트워크")), "1", 10) is None
    print("3) 작품 정보 조회 OK")


def page(nos, total=None, locked=()):
    data = {"articleList": [{"no": n, "subtitle": f"{n}화", **({"thumbnailLock": True} if n in locked else {})} for n in nos]}
    if total is not None: data["pageInfo"] = {"totalPages": total}
    return FakeResponse(200, json_data=data)


async def episode_lists():
    # Scenario 4: 회차 목록 한 페이지 요청의 재시도 — 예외 → HTTP 500 → 성공이면 백오프(1초, 2초)를 기다린 뒤 성공한다
    sleeps.clear()
    flaky = FakeSession(lambda url, params, n: [RuntimeError("끊김"), FakeResponse(500), page([2, 1])][n])
    episodes = await naver_api.fetch_all_episodes(flaky, "1", {}, 10)
    assert [e.episode_no for e in episodes] == [1, 2] and sleeps == [1.0, 2.0] and len(flaky.calls) == 3
    # 끝까지 실패하면 (재시도 횟수+1)번 시도하고 포기 → 빈 목록
    sleeps.clear()
    dead = FakeSession(lambda *a: FakeResponse(500))
    assert await naver_api.fetch_all_episodes(dead, "1", {}, 10) == [] and len(dead.calls) == EPISODE_LIST_MAX_RETRIES + 1
    assert sleeps == [RETRY_BACKOFF_BASE_SECONDS * 2 ** i for i in range(EPISODE_LIST_MAX_RETRIES)]
    # Scenario 5: 여러 페이지는 합쳐서 회차 번호 오름차순, 잠금 표시와 쿠키 전달
    pages = {1: page([5, 4], total=3, locked={4}), 2: page([3, 2]), 3: page([1])}
    session = FakeSession(lambda url, params, n: pages[params["page"]])
    episodes = await naver_api.fetch_all_episodes(session, "9", {"NID_SES": "x"}, 10)
    assert [(e.episode_no, e.subtitle, e.is_locked) for e in episodes] == [(1, "1화", False), (2, "2화", False), (3, "3화", False), (4, "4화", True), (5, "5화", False)]
    assert all(call[0] == NAVER_LIST_URL and call[3] == {"NID_SES": "x"} for call in session.calls) and sorted(c[1]["page"] for c in session.calls) == [1, 2, 3]
    # Scenario 6: 뒤쪽 페이지 하나라도 끝내 실패하면 불완전한 목록을 돌려주지 않고 오류를 낸다 — 빠진 회차를 모른 채 더 큰 번호를 받으면 그 사이 회차가 영영 누락된다
    sleeps.clear()
    holes = {1: page([5, 4], total=3), 2: FakeResponse(500), 3: page([1])}
    try:
        await naver_api.fetch_all_episodes(FakeSession(lambda url, params, n: holes[params["page"]]), "9", {}, 10)
        raise SystemExit("불완전한 목록을 그대로 돌려줌")
    except naver_api.NaverApiError as e:
        assert "9" in str(e) and "2" in str(e), str(e)           # 어느 작품의 몇 페이지가 실패했는지 알려 준다
    # 첫 페이지 실패는 예전처럼 빈 목록(접근 불가 — 성인 미인증 등)
    sleeps.clear()
    assert await naver_api.fetch_all_episodes(FakeSession(lambda *a: FakeResponse(403)), "9", {}, 10) == []
    # Scenario 7: 최신 회차만 가볍게 — 한 페이지에서 가장 큰 번호, 비었거나 실패면 None
    latest_page = FakeSession(lambda *a: page([9, 10, 8], locked={10}))
    latest = await naver_api.fetch_latest_episode(latest_page, "1", 10, {"c": "v"})
    assert latest == EpisodeInfo(episode_no=10, subtitle="10화", is_locked=True) and latest_page.calls[0][3] == {"c": "v"}
    assert await naver_api.fetch_latest_episode_no(FakeSession(lambda *a: page([3, 7])), "1", 10) == 7
    assert await naver_api.fetch_latest_episode(FakeSession(lambda *a: page([])), "1", 10) is None
    sleeps.clear(); assert await naver_api.fetch_latest_episode_no(FakeSession(lambda *a: FakeResponse(500)), "1", 10) is None
    # Scenario 8: 무료 회차만 — 잠긴 첫 회차를 만나면 그 이후는 모두 뺀다(뒤에 풀린 회차가 있어도)
    mixed = [EpisodeInfo(1, "1", False), EpisodeInfo(2, "2", False), EpisodeInfo(3, "3", True), EpisodeInfo(4, "4", False)]
    assert [e.episode_no for e in naver_api.free_episodes_only(mixed)] == [1, 2] and naver_api.free_episodes_only([]) == []
    print("4~8) 회차 목록 OK")


async def weekday_list_and_tags():
    data = {"titleListMap": {
        "MONDAY": [{"titleId": 10, "titleName": "나 작품", "thumbnailUrl": "u2", "author": "A / B", "up": True},
                   {"titleId": 20, "titleName": "가 작품", "rest": True, "finish": True, "adult": True, "new": True}, {"titleName": "id 없음"}, {"titleId": None, "titleName": "id가 null"}, {"titleId": "", "titleName": "id가 빈 문자열"}],
        "TUESDAY": [{"titleId": 10, "titleName": "나 작품"}],
    }}
    session = FakeSession(lambda *a: FakeResponse(200, json_data=data))
    items = await naver_api.fetch_full_webtoon_list(session, 10)
    # Scenario 9: 요일별 목록을 하나로 합치고(같은 작품은 요일만 추가), 제목순으로 정렬, id가 없거나 null/빈 값인 항목은 건너뛴다("None"이라는 가짜 번호로 들어오지 않는다)
    assert [i.title_id for i in items] == ["20", "10"] and session.calls[0][0] == NAVER_WEEKDAY_LIST_URL and session.calls[0][1] == {"order": "user"}
    first, second = items
    assert (first.is_finished, first.is_paused, first.is_adult, first.is_new, first.has_update, first.weekdays) == (True, True, True, True, False, ["MONDAY"])
    assert second.weekdays == ["MONDAY", "TUESDAY"] and second.has_update is True and second.author_summary == "A / B" and second.thumbnail_url == "u2"
    # Scenario 10: 실패는 NaverApiError(원인 포함)로 올린다 — 목록 화면이 502로 보여 준다
    for failing, text in ((FakeSession(lambda *a: FakeResponse(503)), "HTTP 503"), (FakeSession(lambda *a: RuntimeError("끊김")), "끊김")):
        try:
            await naver_api.fetch_full_webtoon_list(failing, 10); raise AssertionError("예외가 나야 한다")
        except naver_api.NaverApiError as e:
            assert text in str(e)
    # Scenario 11: 태그 카탈로그 — 사용자 지정 태그(CUSTOM_TAG)만, text가 없으면 name
    tags = {"tagItemList": [{"type": "CUSTOM_TAG", "id": 134, "text": "먼치킨"}, {"type": "CUSTOM_TAG", "id": 7, "name": "이름만"},
                            {"type": "GENRE", "id": 1, "text": "장르"}, {"type": "CUSTOM_TAG", "text": "id 없음"}]}
    session = FakeSession(lambda *a: FakeResponse(200, json_data=tags))
    assert await naver_api.fetch_tag_catalog(session, 10) == [{"tag_id": "134", "tag_name": "먼치킨"}, {"tag_id": "7", "tag_name": "이름만"}] and session.calls[0][0] == NAVER_TAG_SHORTCUT_URL
    for failing in (FakeSession(lambda *a: FakeResponse(500)), FakeSession(lambda *a: RuntimeError("x"))):
        try:
            await naver_api.fetch_tag_catalog(failing, 10); raise AssertionError("예외가 나야 한다")
        except naver_api.NaverApiError:
            pass
    print("9~11) 요일별 목록/태그 카탈로그 OK")


async def search():
    data = {
        "searchWebtoonResult": {"searchViewList": [
            {"titleId": 11, "titleName": "검색 작품", "thumbnailUrl": "t", "finished": True, "adult": True, "up": True, "rest": True,
             "communityArtists": [{"artistId": 5, "name": "작가5"}, {"artistId": None, "name": "무시"}],
             "genreList": [{"description": "판타지"}, {"description": ""}], "tagList": [{"tagName": "회귀"}, {"tagName": ""}]},
            {"titleId": 11, "titleName": "중복"},
            {"contentId": 99, "titleName": "titleId 없음"},
            {"titleId": 12, "titleName": "두 번째"},
        ]},
        "searchChallengeResult": {"searchViewList": [{"titleId": 13, "titleName": "도전만화"}]},    # 정식 연재 웹툰만 쓴다
    }
    session = FakeSession(lambda *a: FakeResponse(200, json_data=data))
    results = await naver_api.search_webtoons(session, "키워드", 10)
    # Scenario 12: 정식 연재 웹툰 카테고리만, 중복과 titleId 없는 항목은 제외, 작가/장르/태그는 빈 값을 걸러 담는다
    assert [r.title_id for r in results] == ["11", "12"] and session.calls[0][0] == NAVER_SEARCH_ALL_URL and session.calls[0][1] == {"keyword": "키워드"}
    r = results[0]
    assert (r.title_name, r.is_finished, r.is_adult, r.has_update, r.is_paused, r.author_ids_names, r.genres, r.tags) == ("검색 작품", True, True, True, True, [("5", "작가5")], ["판타지"], ["회귀"])
    # Scenario 13: 카테고리가 객체가 아니거나 응답이 실패면 빈 목록(예외를 올리지 않는다)
    assert await naver_api.search_webtoons(FakeSession(lambda *a: FakeResponse(200, json_data={"searchWebtoonResult": []})), "k", 10) == []
    assert await naver_api.search_webtoons(FakeSession(lambda *a: FakeResponse(500)), "k", 10) == []
    assert await naver_api.search_webtoons(FakeSession(lambda *a: RuntimeError("x")), "k", 10) == []
    # Scenario 14: 요일별 목록의 저자 텍스트에서 이름 후보 뽑기 — / , · 로 나누고 중복 제거, 정렬
    items = [NaverListItem("1", "a", "", author_summary="박만사, 남자의 이야기 / 정종택"), NaverListItem("2", "b", "", author_summary="정종택·홍길동"), NaverListItem("3", "c", "", author_summary="")]
    assert naver_api.extract_candidate_author_names(items) == sorted({"박만사", "남자의 이야기", "정종택", "홍길동"})
    print("12~14) 통합검색 OK")


async def artist_and_curation():
    # Scenario 15: 작가의 다른 작품 — 200이면 응답 그대로, 실패나 예외면 빈 목록
    other = FakeSession(lambda *a: FakeResponse(200, json_data=[{"titleId": 5}]))
    assert await naver_api.fetch_other_titles_by_artist(other, "1", 10) == [{"titleId": 5}] and other.calls[0][0] == NAVER_ARTIST_OTHER_TITLES_URL
    assert await naver_api.fetch_other_titles_by_artist(FakeSession(lambda *a: FakeResponse(500)), "1", 10) == []
    assert await naver_api.fetch_other_titles_by_artist(FakeSession(lambda *a: RuntimeError("x")), "1", 10) == []
    # Scenario 16: 태그 이름 — 못 읽으면 태그 번호 문자열
    named = FakeSession(lambda *a: FakeResponse(200, json_data={"curationTitle": "먼치킨"}))
    assert await naver_api.fetch_curation_title_name(named, 134, 10) == "먼치킨" and named.calls[0][0] == NAVER_CURATION_META_URL
    assert await naver_api.fetch_curation_title_name(FakeSession(lambda *a: FakeResponse(200, json_data={})), 134, 10) == "134"
    assert await naver_api.fetch_curation_title_name(FakeSession(lambda *a: FakeResponse(404)), 134, 10) == "134"
    assert await naver_api.fetch_curation_title_name(FakeSession(lambda *a: RuntimeError("x")), 134, 10) == "134"
    # Scenario 17: 태그의 작품은 totalPages까지 페이지를 이어 받고(페이지 사이에 간격), 도중에 실패하면 거기까지만
    sleeps.clear()
    pages = {1: {"curationViewList": [{"titleId": 1}], "pageInfo": {"totalPages": 3}}, 2: {"curationViewList": [{"titleId": 2}], "pageInfo": {"totalPages": 3}}, 3: {"curationViewList": [{"titleId": 3}], "pageInfo": {"totalPages": 3}}}
    session = FakeSession(lambda url, params, n: FakeResponse(200, json_data=pages[params["page"]]))
    assert [i["titleId"] for i in await naver_api.fetch_curation_titles(session, 134, 10, 0.5)] == [1, 2, 3] and sleeps == [0.5, 0.5]
    assert all(c[0] == NAVER_CURATION_LIST_URL and c[1]["pageSize"] == 100 for c in session.calls)
    cut = FakeSession(lambda url, params, n: FakeResponse(200, json_data=pages[1]) if params["page"] == 1 else FakeResponse(500))
    assert [i["titleId"] for i in await naver_api.fetch_curation_titles(cut, 134, 10, 0)] == [1]
    assert await naver_api.fetch_curation_titles(FakeSession(lambda *a: RuntimeError("x")), 134, 10, 0) == []
    assert len(await naver_api.fetch_curation_titles(FakeSession(lambda *a: FakeResponse(200, json_data={"curationViewList": [{"titleId": 1}]})), 134, 10, 0)) == 1   # 페이지 정보가 없으면 한 페이지
    print("15~17) 작가의 다른 작품/태그 작품 OK")


parse_title_info()
asyncio.run(title_info_fetch())
asyncio.run(episode_lists())
asyncio.run(weekday_list_and_tags())
asyncio.run(search())
asyncio.run(artist_and_curation())
print("ALL OK")
