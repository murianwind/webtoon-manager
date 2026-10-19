"""구독 실행 이력 — 구독할 때마다(네이버/카카오 수동 구독, 카카오 신작 스캔 자동 구독) 구독했다는 사실과 작가 등록 결과를 실행 이력("subscription")에 한 건씩 남긴다."""
import asyncio
from types import SimpleNamespace as NS

import httpx

from app import db, repository, naver_api, tracker, kakao_page_download as kpd
db.get_connection()
import app.main as m
from app.api import webtoons as webtoons_api
from app.models import TitleInfo

JOB = "subscription"


def entries():
    """가장 오래된 것부터."""
    return [e for e in reversed(repository.list_job_history(100)) if e["job_name"] == JOB]


def last():
    return entries()[-1]


def text(entry):
    return "\n".join(line.split(" — ", 1)[1] for line in entry["log"])


def kakao_about(*roles):
    return {"author_list": [{"name": n, "role": r} for n, r in roles]}


ABOUT = {}
async def fake_about(self, series_id):
    return ABOUT.get(series_id)
kpd.KakaoPageClient.fetch_about = fake_about

KAKAO_BODY = {"title": "카카오작품", "thumbnail_url": "u", "author_summary": "글쓴이, 원작가"}


async def kakao_scenarios():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 1. 카카오 구독: 구독 + 원작자 등록이 이력 한 건으로 남는다 ──
        ABOUT[1] = kakao_about(("글쓴이", "writer"), ("그린이", "illustrator"), ("원작가", "original_author"))
        assert (await c.post("/api/kakao-webtoons/1/subscribe", json=KAKAO_BODY)).status_code == 200
        e = last()
        assert len(entries()) == 1 and e["status"] == "success" and e["started_at"] and e["finished_at"], e
        assert "[카카오] 카카오작품 — 구독" in text(e) and "[카카오] 카카오작품 — 원작자 등록: 원작가(신규)" in text(e), text(e)
        assert all(" — " in line for line in e["log"])                                                              # 화면의 로그 표시 형식("시각 — 내용")
        print("1) 카카오 구독 이력 OK (구독 + 원작자 등록)")

        # ── 2. 같은 작가를 또 구독하면 '이미 등록됨', 사용자가 직접 꺼 둔 작가는 '직접 꺼 둔 작가라 켜지 않음' ──
        await c.post("/api/kakao-webtoons/1/subscribe", json=KAKAO_BODY)
        assert "원작자 등록: 원작가(이미 등록됨)" in text(last()) and len(entries()) == 2
        repository.set_watched_author_enabled("원작가", False, "원작가", "kakao")
        await c.post("/api/kakao-webtoons/1/subscribe", json=KAKAO_BODY)
        assert "원작가(직접 꺼 둔 작가라 켜지 않음)" in text(last()) and {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}["원작가"] is False
        print("2) 이미 등록됨 / 직접 꺼 둔 작가라 켜지 않음 OK")

        # ── 2-1. 앱이 후보로 채워 둔 꺼진 작가(구독 전에 이미 훑은 작품 등)는 구독하면 켜지고 '후보에서 등록'으로 남는다 ──
        repository.upsert_watched_author("후보원작가", "후보원작가", False, "kakao")
        ABOUT[2] = kakao_about(("후보원작가", "original_author"))
        await c.post("/api/kakao-webtoons/2/subscribe", json={**KAKAO_BODY, "title": "후보 작품"})
        assert "[카카오] 후보 작품 — 원작자 등록: 후보원작가(후보에서 등록)" in text(last()), text(last())
        assert {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}["후보원작가"] is True
        print("2-1) 카카오 후보 작가 구독 시 켜짐 OK")

        # ── 3. 원작자가 없으면 글 작가 (여러 명이면 모두) ──
        ABOUT[3] = kakao_about(("글A", "writer"), ("글B", "writer"), ("그린이", "illustrator"))
        await c.post("/api/kakao-webtoons/3/subscribe", json={**KAKAO_BODY, "title": "글만 있는 작품"})
        assert "[카카오] 글만 있는 작품 — 작가 등록: 글A(신규), 글B(신규)" in text(last()), text(last())
        print("3) 글 작가 등록 이력 OK")

        # ── 4. 작품 정보를 못 받으면 사유를 남기고 이력은 실패(error)로 표시 ──
        await c.post("/api/kakao-webtoons/4/subscribe", json={**KAKAO_BODY, "title": "정보 없는 작품"})
        e = last()
        assert e["status"] == "error" and "[카카오] 정보 없는 작품 — 구독" in text(e) and "작품 정보 조회 실패 — 작가 등록을 건너뜀" in text(e), text(e)
        print("4) 정보 조회 실패 이력 OK")

        # ── 5. 정보는 받았는데 인식되는 작가가 없는 경우 ──
        ABOUT[5] = kakao_about(("그린이", "illustrator"), ("누구", "unknown_role"))
        await c.post("/api/kakao-webtoons/5/subscribe", json={**KAKAO_BODY, "title": "작가 없는 작품"})
        e = last()
        assert e["status"] == "success" and "[카카오] 작가 없는 작품 — 작가 정보 없음 (글/원작 작가가 없음)" in text(e), text(e)
        print("5) 작가 정보 없음 이력 OK")

        # ── 6. 자동 등록 설정이 꺼져 있으면 등록하지 않았다고 남긴다 ──
        repository.set_setting("auto_register_author_on_subscribe", "0")
        before = {a.author_id for a in repository.list_watched_authors("kakao")}
        ABOUT[6] = kakao_about(("새작가", "writer"))
        await c.post("/api/kakao-webtoons/6/subscribe", json={**KAKAO_BODY, "title": "설정 끈 작품"})
        assert "[카카오] 설정 끈 작품 — 작가 자동 등록 설정이 꺼져 있어 등록하지 않음" in text(last())
        assert {a.author_id for a in repository.list_watched_authors("kakao")} == before
        repository.set_setting("auto_register_author_on_subscribe", None)
        print("6) 자동 등록 설정 끔 이력 OK")

        # ── 7. 제외/구독해제는 구독 이력이 아니다 ──
        count = len(entries())
        await c.post("/api/kakao-webtoons/6/exclude", json=KAKAO_BODY)
        await c.post("/api/kakao-webtoons/1/unsubscribe")
        assert len(entries()) == count
        print("7) 제외/구독해제는 기록 안 함 OK")


async def kakao_scan_scenario():
    # ── 8. 카카오 신작 스캔이 자동 구독한 작품도 같은 이력(출처 표시)을 남긴다 ──
    repository.upsert_new_kakao_webtoon(80, "스캔신작", status=repository.STATUS_ACTIVE)
    ABOUT[80] = kakao_about(("스캔원작", "original_author"))
    before = len(entries())
    await tracker._record_kakao_title_authors(80, NS(request_timeout_seconds=5))
    e = last()
    assert len(entries()) == before + 1 and e["status"] == "success"
    assert "[카카오] 스캔신작 — 신작 스캔으로 자동 구독" in text(e) and "원작자 등록: 스캔원작(신규)" in text(e), text(e)
    ABOUT.pop(80)
    await tracker._record_kakao_title_authors(80, NS(request_timeout_seconds=5))
    assert last()["status"] == "error" and "작품 정보 조회 실패" in text(last())
    print("8) 신작 스캔 자동 구독 이력 OK")


def title_info(*, origin=(), writers=()):
    return TitleInfo(title_id="", title_name="", synopsis="", is_adult=False, webtoon_type="webtoon", is_finished=False, thumbnail_url="u",
                     writer_id_name_pairs=list(writers), origin_id_name_pairs=list(origin))


async def naver_scenarios():
    infos = {}
    async def fake_fetch(session, title_id, timeout):
        return infos.get(title_id)
    naver_api.fetch_title_info = fake_fetch
    for tid in ("100", "101", "102", "103"):
        repository.upsert_new(title_id=tid, title=f"작품{tid}")

    # ── 9. 네이버 구독: 구독 + 원작자 등록 ──
    infos["100"] = title_info(origin=[("3", "원작자")], writers=[("1", "글작가")])
    before = len(entries())
    await webtoons_api._enrich_and_record("100", True)
    e = last()
    assert len(entries()) == before + 1 and e["status"] == "success"
    assert "[네이버] 작품100 — 구독" in text(e) and "[네이버] 작품100 — 원작자 등록: 원작자(신규)" in text(e), text(e)
    print("9) 네이버 구독 이력 OK")

    # ── 10. 이미 등록 / 꺼 둔 작가 ──
    await webtoons_api._enrich_and_record("100", True)
    assert "원작자 등록: 원작자(이미 등록됨)" in text(last())
    repository.set_watched_author_enabled("3", False, "원작자")
    await webtoons_api._enrich_and_record("100", True)
    assert "원작자(직접 꺼 둔 작가라 켜지 않음)" in text(last())
    print("10) 네이버 이미 등록됨 / 직접 꺼 둔 작가 OK")

    # ── 10-1. 네이버도 앱이 후보로 채워 둔 꺼진 작가는 구독하면 켜진다 ──
    infos["101"] = title_info(writers=[("9", "후보글작가")])
    await webtoons_api._enrich_and_record("101", False)                                                              # 목록/제외 상태에서 후보로 채움(꺼진 채)
    assert {a.author_id: a.enabled for a in repository.list_watched_authors()}["9"] is False
    await webtoons_api._enrich_and_record("101", True)                                                               # 이제 구독
    assert "[네이버] 작품101 — 작가 등록: 후보글작가(후보에서 등록)" in text(last()), text(last())
    assert {a.author_id: a.enabled for a in repository.list_watched_authors()}["9"] is True
    print("10-1) 네이버 후보 작가 구독 시 켜짐 OK")

    # ── 11. 설정이 꺼져 있으면(register=False) 켜지 않고 전체 작가 목록에만 올렸다고 남긴다 ──
    infos["101"] = title_info(writers=[("5", "글작가5")])
    await webtoons_api._enrich_and_record("101", False)
    assert "[네이버] 작품101 — 작가 자동 등록 설정이 꺼져 있어 켜지 않고 전체 작가 목록에만 올림: 글작가5" in text(last()), text(last())
    assert {a.author_id: a.enabled for a in repository.list_watched_authors()}["5"] is False
    print("11) 네이버 설정 끔 이력 OK")

    # ── 12. 작가 정보가 비었거나 정보 조회가 실패한 경우 ──
    infos["102"] = title_info()
    await webtoons_api._enrich_and_record("102", True)
    assert last()["status"] == "success" and "[네이버] 작품102 — 작가 정보 없음" in text(last()), text(last())
    await webtoons_api._enrich_and_record("103", True)                                                              # infos에 없음 → 조회 실패
    e = last()
    assert e["status"] == "error" and "[네이버] 작품103 — 구독" in text(e) and "정보 조회 실패" in text(e), text(e)
    print("12) 네이버 작가 없음 / 조회 실패 이력 OK")


async def main():
    await kakao_scenarios()
    await kakao_scan_scenario()
    await naver_scenarios()


asyncio.run(main())
print("구독 실행 이력 OK")
