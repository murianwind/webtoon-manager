"""리포트 작업 특성 테스트 — 최초 실행, 테스트 발송(오늘/어제 대체), 뷰어 링크 조회, 수집 옵션. (리팩터링 전후 동작이 같음을 고정한다)"""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify, webtoon_server_client
db.get_connection()

sent = []; lookups = []; collected = []
async def fake_send(session, settings, message): sent.append(message)
discord_notify.send_webhook_notification = fake_send
async def fake_naver(session, settings): collected.append("naver"); return []
async def fake_kakao(session, settings): collected.append("kakao"); return []
scheduler._collect_unregistered_new_episodes = fake_naver; scheduler._collect_kakao_new_episodes = fake_kakao
VIEWER = {}
async def fake_reader(session, base, name, timeout): lookups.append(name); return VIEWER.get(name)
webtoon_server_client.fetch_reader_url = fake_reader

def reset():
    sent.clear(); lookups.clear(); collected.clear(); job_status.start("report")
async def run(force=False): reset(); await scheduler._run_report_job_impl(force_test=force); return list(sent), job_status.snapshot()["report"]
def log_of(s): return "\n".join(s["log"])
def add_row(title_id, name, no, platform, when=None, status="success"):
    repository.add_episode_history(title_id, name, no, f"{no}화", status, platform=platform)
    if when:
        with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE id = (SELECT MAX(id) FROM episode_history)", (when,))

async def main():
    SINCE = scheduler._SETTING_KEY_REPORT_LAST_SENT_AT
    # ── 1. 최초 실행: 지금부터 집계 시작(발송 없음), 기록은 건드리지 않음 ──
    add_row("845332", "옛이력", 1, "naver")
    msgs, s = await run()
    assert msgs == [] and s["status"] == "success" and "최초 실행" in log_of(s) and repository.get_setting(SINCE)
    print("1) 최초 실행 OK")

    # ── 2. 일반 발송: 마지막 발송 이후의 이력만, 끝나면 시각 갱신 ──
    add_row("845332", "네이버작품", 2, "naver"); old_since = repository.get_setting(SINCE)
    msgs, s = await run()
    assert len(msgs) == 1 and "네이버작품" in msgs[0] and "옛이력" not in msgs[0] and "리포트 발송 완료 (성공 1건, 실패 0건)" in log_of(s)
    assert repository.get_setting(SINCE) > old_since
    msgs, s = await run(); assert msgs == [] and "발송할 내용 없음" in log_of(s)                                        # 새 이력이 없으면 안 보냄(시각은 갱신)
    print("2) 일반 발송 OK")

    # ── 3. 테스트 발송: 오늘 것만(시각 기준 무시), 없으면 어제 것으로 대체하고 표시 ──
    since_before = repository.get_setting(SINCE)
    msgs, s = await run(force=True)
    assert len(msgs) == 1 and "옛이력" in msgs[0] and "네이버작품" in msgs[0] and not msgs[0].startswith("🧪") and repository.get_setting(SINCE) == since_before   # 오늘 이력 전부, 시각은 그대로
    with db.write_transaction() as cx:                                                                               # 오늘 이력을 전부 어제로 옮겨서 "오늘 없음" 상황을 만든다
        cx.execute("UPDATE episode_history SET downloaded_at = ?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    msgs, s = await run(force=True)
    assert len(msgs) == 1 and msgs[0].startswith("🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**") and "네이버작품" in msgs[0]
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ?", ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),))
    msgs, s = await run(force=True); assert msgs == [] and "발송할 내용 없음" in log_of(s)                               # 오늘도 어제도 없으면 안 보냄
    print("3) 테스트 발송 OK (오늘/어제 대체/없음)")

    # ── 4. 뷰어 링크: 웹툰 뷰어 서버가 설정돼 있으면 플랫폼별 폴더 이름 규칙으로 조회 ──
    repository.set_setting("webtoon_server_url", "http://viewer")
    repository.set_setting(SINCE, datetime.now(timezone.utc).isoformat())   # 여기까지의 이력은 이미 집계한 것으로 치고, 이후 이력만 본다
    add_row("845332", "제목 : 부제", 3, "naver"); add_row("61641075", "도굴왕: 엔드라인", 4, "kakao"); add_row("68239972", "개미", 5, "kakao")
    VIEWER.update({"제목 ： 부제": "http://viewer/n1", "도굴왕_ 엔드라인": "http://viewer/k1"})                              # 조회 키: 네이버=전각 치환, 카카오=밑줄 규칙
    msgs, s = await run()
    assert sorted(lookups) == sorted(["제목 ： 부제", "도굴왕_ 엔드라인", "개미"]), lookups
    m = msgs[0]
    assert "• [네이버] 제목 : 부제 [바로가기](<http://viewer/n1>)" in m and "• [카카오] 도굴왕: 엔드라인 · 1개 회차(4번) [바로가기](<http://viewer/k1>)" in m
    assert "• [카카오] 개미 · 1개 회차(5번) [바로가기](<https://page.kakao.com/content/68239972>)" in m                      # 뷰어에 없으면 카카오페이지 링크
    repository.set_setting("webtoon_server_url", None); repository.set_setting(SINCE, datetime.now(timezone.utc).isoformat())
    add_row("845332", "뷰어없음", 6, "naver"); msgs, s = await run(); assert lookups == [] and "• 뷰어없음" in msgs[0] and "바로가기" not in msgs[0].split("뷰어없음")[1].split("\n")[0]   # 뷰어 서버가 없으면 조회 안 함
    print("4) 뷰어 링크 OK")

    # ── 5. 수집 옵션: 새 에피소드 확인을 끄면/카카오 관리를 끄면 해당 수집을 안 한다 ──
    repository.set_setting("kakao_webtoons_enabled", "0"); msgs, s = await run(force=True); assert collected == ["naver"]
    repository.set_setting("kakao_webtoons_enabled", "1"); msgs, s = await run(force=True); assert collected == ["naver", "kakao"]
    repository.set_setting("report_unregistered_new_episodes_enabled", "0"); msgs, s = await run(force=True); assert collected == []
    print("5) 수집 옵션 OK")
asyncio.run(main())
print("\n전부 통과")
