"""카카오 신작 스캔으로 자동 추가된 작품의 작가 처리 — 원작자 우선 등록(구독할 때와 같은 규칙), 글/원작 이름 저장, 실패/설정 끔 처리."""
import asyncio
from types import SimpleNamespace as NS

from app import db, repository, tracker, job_status, kakao_api, discord_notify, kakao_page_download as kp
db.get_connection()

S = NS(request_timeout_seconds=5, delay_seconds=0)
sent = []; results = {}; ABOUT = {}
async def fake_send(session, settings, message): sent.append(message)
async def fake_search(session, author_name, timeout): return list(results.get(author_name, []))
async def fake_about(self, series_id): return ABOUT.get(series_id)
kakao_api.search_by_author = fake_search; discord_notify.send_webhook_notification = fake_send; kp.KakaoPageClient.fetch_about = fake_about
_o = asyncio.sleep
async def _ns(x): await _o(0)
tracker.asyncio.sleep = _ns

def card(i, title, authors=("작가A",)): return {"title_id": i, "title_name": title, "thumbnail_url": f"https://k/{i}", "author_names": list(authors), "is_adult": False}
def about(writers=(), illustrators=(), originals=()):
    return {"author_list": [{"name": n, "role": "writer"} for n in writers] + [{"name": n, "role": "illustrator"} for n in illustrators] + [{"name": n, "role": "original_author"} for n in originals]}
async def scan(): job_status.start("discovery"); sent.clear(); return await tracker.scan_kakao_authors_for_new_titles(None, S)
def watched(): return {a.author_id: a.enabled for a in repository.list_watched_authors("kakao")}

async def main():
    repository.set_setting("kakao_webtoons_enabled", "1")
    repository.upsert_watched_author("작가A", "작가A", True, "kakao")
    results["작가A"] = [card(100, "기존작")]
    assert await scan() == 0                                                                                      # 첫 스캔은 기준선만

    # ── 1. 원작자가 있으면 원작자를 등록하고(글 작가는 안 함), 글/원작 이름은 작품 기록에 저장 ──
    results["작가A"].append(card(101, "원작있는신작", ("작가A", "글쓴이"))); ABOUT[101] = about(["글쓴이"], ["그림가"], ["원작가1", "원작가2"])
    assert await scan() == 1
    assert watched() == {"작가A": True, "원작가1": True, "원작가2": True}, watched()
    w = repository.get_kakao_webtoon(101); assert w["writer_names"] == ["글쓴이"] and w["origin_names"] == ["원작가1", "원작가2"] and w["status"] == "active"
    assert len(sent) == 1 and "카카오웹툰 신작 자동 추가" in sent[0]
    print("1) 원작자 우선 등록 OK (여러 명은 각각)")

    # ── 2. 원작자가 없으면 글 작가 ──
    results["작가A"].append(card(102, "글만있는신작")); ABOUT[102] = about(["글A", "글B"], ["그림가"])
    assert await scan() == 1 and watched() == {"작가A": True, "원작가1": True, "원작가2": True, "글A": True, "글B": True}
    print("2) 글 작가 등록 OK")

    # ── 3. 작품 정보를 못 받아도 작품 추가와 알림은 그대로, 작가 등록만 건너뜀 ──
    results["작가A"].append(card(103, "정보없는신작")); before = dict(watched())
    assert await scan() == 1 and repository.get_kakao_webtoon(103)["status"] == "active" and len(sent) == 1 and watched() == before
    assert repository.get_kakao_webtoon(103)["writer_names"] == [] and repository.get_kakao_webtoon(103)["origin_names"] == []
    async def boom(self, series_id): raise RuntimeError("네트워크 오류")                                            # 예외도 격리
    kp.KakaoPageClient.fetch_about = boom
    results["작가A"].append(card(104, "예외신작")); assert await scan() == 1 and repository.get_kakao_webtoon(104)["status"] == "active"
    kp.KakaoPageClient.fetch_about = fake_about
    print("3) 정보 조회 실패 격리 OK")

    # ── 4. 사용자가 꺼 둔 작가는 다시 켜지 않는다 ──
    repository.upsert_watched_author("꺼둔원작가", "꺼둔원작가", False, "kakao")
    results["작가A"].append(card(105, "꺼둔작가의신작")); ABOUT[105] = about(["글C"], [], ["꺼둔원작가"])
    assert await scan() == 1 and watched()["꺼둔원작가"] is False and "글C" not in watched()
    print("4) 꺼 둔 작가 유지 OK")

    # ── 5. "구독할 때 작가 자동 등록"을 끄면 등록하지 않는다(이름 저장은 그대로) ──
    repository.set_setting("auto_register_author_on_subscribe", "0")
    results["작가A"].append(card(106, "설정끔신작")); ABOUT[106] = about(["글D"], [], ["원작D"]); before = dict(watched())
    assert await scan() == 1 and watched() == before and repository.get_kakao_webtoon(106)["origin_names"] == ["원작D"]
    repository.set_setting("auto_register_author_on_subscribe", None)
    print("5) 설정 끔 OK")
asyncio.run(main())
print("\n전부 통과")
