"""리포트 작업 특성 테스트 — 기준 시각이 없을 때, 묶음 발송, 테스트 발송(오늘/어제 대체), 뷰어 링크 조회, 수집 옵션. (리팩터링 전후 동작이 같음을 고정한다)
옛 "until 없는 일반 발송" 경로는 운영에서 부르는 곳이 없어서 없앴다 — 일반 발송은 send_batch_report(묶음의 끝), 테스트 발송은 run_test_report다."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db, repository, scheduler, job_status, discord_notify, webtoon_server_client
from fakes import discord_fake
db.get_connection()

sent = []; lookups = []; collected = []
fake_send = discord_fake(sent, result=None)
discord_notify.send_webhook_notification = fake_send
NEW = {"naver": [], "kakao": []}                                                                                        # 수집 결과(새 에피소드 요약) — 시나리오에서 바꾼다
async def fake_naver(session, settings): collected.append("naver"); return list(NEW["naver"])
async def fake_kakao(session, settings): collected.append("kakao"); return list(NEW["kakao"])
scheduler.report_sections.collect_unregistered_new_episodes = fake_naver; scheduler.report_sections.collect_kakao_new_episodes = fake_kakao
async def no_new_titles(session, settings): return []                                                             # 신규 웹툰 수집은 이 테스트의 관심사가 아니다 — 실제 네이버/카카오 목록을 부르지 않게(CI에서만 실패한 적이 있다)
scheduler.report_sections.collect_naver_new_titles = no_new_titles; scheduler.report_sections.collect_kakao_new_titles = no_new_titles
VIEWER = {}
async def fake_reader(session, base, name, timeout): lookups.append(name); return VIEWER.get(name)
webtoon_server_client.fetch_reader_url = fake_reader

def reset():
    sent.clear(); lookups.clear(); collected.clear(); job_status.start("report")
async def run(force=False):
    reset()
    if force: await scheduler.run_test_report()                                                                         # 리포트 발송 버튼(테스트 발송)
    else: await scheduler.send_batch_report(datetime.now(timezone.utc).isoformat())                                    # 1분 확인이 묶음을 확정하고 부르는 발송(묶음의 끝 = 지금)
    return list(sent), job_status.snapshot()["report"]
def log_of(s): return "\n".join(s["log"])
def add_row(title_id, name, no, platform, when=None, status="success"):
    repository.add_episode_history(title_id, name, no, f"{no}화", status, platform=platform)
    if when:
        with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE id = (SELECT MAX(id) FROM episode_history)", (when,))

async def main():
    SINCE = scheduler._SETTING_KEY_REPORT_LAST_SENT_AT
    # ── 1. 기준 시각이 없으면(처음) 옛 이력을 몰아 보내지 않는다: 보낼 이력이 없는 것으로 보고 이번 묶음의 끝을 기준으로 삼는다(발송 없음). 기록은 건드리지 않음 ──
    add_row("845332", "옛이력", 1, "naver")
    msgs, s = await run()
    assert msgs == [] and s["status"] == "success" and "발송할 내용 없음" in log_of(s) and repository.get_setting(SINCE)
    print("1) 기준 시각이 없을 때 OK")

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

    # ── 6. 요약만 있고 이력이 없을 때 — 자동 발송은 보내지 않고(새 에피소드 요약은 이력과 함께만 나간다) 발송 기준 시각만 옮긴다, 테스트 발송은 요약만 있어도 보낸다(오늘/어제 이력이 없을 때) ──
    repository.set_setting("report_unregistered_new_episodes_enabled", "1")
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ?", ((datetime.now(timezone.utc) - timedelta(days=9)).isoformat(),))   # 오늘/어제 이력 없음
    NEW["naver"] = [("10", "요약만있는작", 7)]; repository.set_setting(SINCE, (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()); before_since = repository.get_setting(SINCE)
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ?", ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),))    # 기준 시각 이후의 이력도 없음
    msgs, s = await run(); assert msgs == [] and "발송할 내용 없음" in log_of(s) and repository.get_setting(SINCE) > before_since, "자동 발송: 이력이 없으면 요약만으로는 안 보낸다"
    msgs, s = await run(force=True); assert len(msgs) == 1 and "요약만있는작" in msgs[0] and "받은 작품 (0)" in msgs[0], "테스트 발송: 요약만 있어도 보낸다"
    NEW["naver"] = []
    print("6) 요약만 있을 때 OK")

    # ── 7. 이미 실행 중이면(리포트 작업 잠금) 테스트 발송은 건너뛴다 — 겹쳐서 두 번 보내지 않게 ──
    NEW["naver"] = [("11", "겹침확인작", 1)]
    async def overlapped():
        async with scheduler.report_job._report_job_lock:
            reset(); await scheduler.run_test_report()
    await asyncio.wait_for(overlapped(), 3)
    assert sent == [] and "이미 실행 중이라 건너뜁니다" in log_of(job_status.snapshot()["report"]), sent
    NEW["naver"] = []
    print("7) 겹침 방지 OK")
asyncio.run(main())
print("\n전부 통과")
