"""웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)이 실제로 어떻게 나가는지 — 하루 한 번, 규칙은 test_list_alert.py(언제)와 test_new_titles.py(무엇을).
- 발송하지 않는 시간 방식: 그날 가장 빠른 종료 시각 이후 처음 나가는 다운로드 리포트에 얹혀 나가고, 30분 안에 리포트가 안 나가면 1분 확인이 이 알림만 묶어서 보낸다.
- 시각 방식: 다운로드 리포트에는 안 얹고 그 시각에 이 알림만 보낸다. 보낼 것이 없는 날도 "그날은 확인했다"로 쳐서 하루 한 번이다. 전송이 실패하면 그날 안에 다시 시도한다(10분 간격).
- 테스트 발송 버튼은 예전처럼 전부 보여 준다.
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import db, discord_config, job_status, list_alert, report_dispatch, report_seen, repository
db.get_connection()
import app.scheduler as scheduler
from app.scheduler import report_tick

KST = ZoneInfo("Asia/Seoul")
def at(day, h, mi=0): return datetime(2026, 11, day, h, mi, tzinfo=KST).astimezone(timezone.utc)
q = lambda sh, sm, eh, em, days=(): {"start_hour": sh, "start_minute": sm, "end_hour": eh, "end_minute": em, "days": list(days)}

sent = []; FAIL = {"on": False}
async def fake_send(session, message):
    if FAIL["on"]: raise RuntimeError("웹훅 전송 실패")
    sent.append(message)
scheduler.report_job.discord_notify.send_webhook_notification = fake_send
NEW_N, NEW_K, EP_N, EP_K = [], [], [], []
async def fake_new_n(session, settings): return list(NEW_N)
async def fake_new_k(session, settings): return list(NEW_K)
async def fake_ep_n(session, settings): return list(EP_N)
async def fake_ep_k(session, settings): return list(EP_K)
rs = scheduler.report_sections
rs.collect_naver_new_titles, rs.collect_kakao_new_titles, rs.collect_unregistered_new_episodes, rs.collect_kakao_new_episodes = fake_new_n, fake_new_k, fake_ep_n, fake_ep_k
repository.set_setting("kakao_webtoons_enabled", "1"); discord_config.set_webhook_url("https://discord.test/hook")

counter = [0]
def download_row(now):
    counter[0] += 1; repository.add_episode_history("90000", f"받은작품{counter[0]}", counter[0], f"{counter[0]}화", "success", platform="naver")
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE title_name = ?", (now.isoformat(), f"받은작품{counter[0]}"))   # 이 시나리오의 가짜 시계에 맞춘다
async def batch(now, row=True):
    """다운로드 리포트 한 번(묶음의 끝 = now) — 새 다운로드 이력을 하나 넣고 보낸다."""
    before = len(sent)
    if row: download_row(now)
    await scheduler.send_batch_report(now.isoformat(), now=now)
    return sent[before:]
async def tick(now):
    before = len(sent); await report_tick.run_report_tick(now); return sent[before:]
def reset(quiet=(), time=None):
    for key in (list_alert.SETTING_DONE, list_alert.SETTING_TIME, report_seen.SETTING_KEY, "list_alert_retry_at"): repository.set_setting(key, None)
    report_dispatch.set_quiet_hours(list(quiet)); sent.clear(); FAIL["on"] = False
    NEW_N[:] = [("12", "신규 네이버")]; NEW_K[:] = [(22, "신규 카카오")]; EP_N[:] = [("11", "새회차 네이버", 5)]; EP_K[:] = [(33, "새회차 카카오", "https://page.kakao.com/content/33/viewer/9")]
    repository.set_setting(scheduler.report_job._SETTING_KEY_REPORT_LAST_SENT_AT, at(1, 0).isoformat())
    if time: list_alert.set_time(time)
has_sections = lambda m: "신규 웹툰" in m and "새 에피소드" in m
no_sections = lambda m: "신규 웹툰" not in m and "새 에피소드" not in m

async def main():
    # Scenario 1: 발송하지 않는 시간 방식 — 06:00 이전의 리포트에는 안 얹고, 이후 처음 나가는 리포트에 얹는다(신규 웹툰 + 새 에피소드). 같은 날 다음 리포트에는 없다. 다음 날 다시
    reset([q(23, 0, 6, 0)])
    (m,) = await batch(at(3, 5, 30)); assert no_sections(m) and "받은작품" in m, "끝나기 전(05:30)에는 안 얹는다"
    (m,) = await batch(at(3, 7, 0)); assert has_sections(m) and "신규 네이버" in m and "새회차 카카오" in m and "정보 보기" in m, m
    (m,) = await batch(at(3, 12, 0)); assert no_sections(m), "같은 날 두 번째 리포트에는 없다(하루 한 번)"
    NEW_N[:] = [("12", "신규 네이버"), ("13", "다음날 신규")]; EP_N[:] = [("11", "새회차 네이버", 5)]                                    # 어제 알린 것은 그대로 목록에 있고 하나가 더 생겼다
    (m,) = await batch(at(4, 7, 0)); assert "다음날 신규" in m and "신규 네이버" not in m and "새회차" not in m, "다음 날: 아직 안 알린 것만(같은 회차/신규는 반복 안 함)"
    print("1) 리포트에 얹기 OK")

    # Scenario 2: 얹을 차례였는데 알릴 것이 없으면 그날은 확인한 것으로 친다 — 늦게 생긴 항목은 다음 날 알린다
    reset([q(23, 0, 6, 0)]); NEW_N.clear(); NEW_K.clear(); EP_N.clear(); EP_K.clear()
    (m,) = await batch(at(3, 7, 0)); assert no_sections(m)
    NEW_N[:] = [("14", "늦게생긴신규")]
    (m,) = await batch(at(3, 9, 0)); assert no_sections(m), "그날 이미 확인했다"
    (m,) = await batch(at(4, 7, 0)); assert "늦게생긴신규" in m
    print("2) 알릴 것이 없는 날 OK")

    # Scenario 3: 시각 방식 — 다운로드 리포트에는 절대 안 얹고, 그 시각에 1분 확인이 이 알림만 보낸다(🔔 제목, 다운로드 이력 없음). 하루 한 번
    reset(time="09:00")
    (m,) = await batch(at(3, 8, 0)); assert no_sections(m); (m,) = await batch(at(3, 12, 0)); assert no_sections(m), "시각 방식은 다운로드 리포트에 안 얹는다"
    reset(time="09:00"); assert await tick(at(3, 8, 59)) == [], "시각 전"
    (m,) = await tick(at(3, 9, 0)); assert m.startswith("🔔 웹툰 전체목록 알림") and has_sections(m) and "받은 작품" not in m, m
    assert await tick(at(3, 9, 1)) == [] and await tick(at(3, 18, 0)) == [], "그날은 한 번만"
    NEW_N[:] = [("12", "신규 네이버"), ("15", "다음날 신규")]; (m,) = await tick(at(4, 9, 0)); assert "다음날 신규" in m and "신규 네이버" not in m and "새회차" not in m, "단독 발송도 어제 알린 것은 반복하지 않는다"
    print("3) 시각 방식 단독 발송 OK")

    # Scenario 4: 시각에 알릴 것이 없으면 아무것도 안 보내고 그날은 끝(늦게 생긴 것은 다음 날) / 디스코드가 없으면 안 보낸다
    reset(time="09:00"); NEW_N.clear(); NEW_K.clear(); EP_N.clear(); EP_K.clear()
    assert await tick(at(3, 9, 0)) == []; NEW_N[:] = [("16", "늦게생긴")]; assert await tick(at(3, 10, 0)) == [], "그날은 이미 확인했다"
    (m,) = await tick(at(4, 9, 0)); assert "늦게생긴" in m
    print("4) 시각에 알릴 것이 없을 때 OK")

    # Scenario 5: 전송이 실패하면 "했다"로 치지 않고 10분 뒤에 다시 시도한다(그날 안에). 성공하면 그날 끝
    reset(time="09:00"); FAIL["on"] = True
    assert await tick(at(3, 9, 0)) == [] and not list_alert.is_done(at(3, 9, 0)), "실패는 기록하지 않는다"
    FAIL["on"] = False; assert await tick(at(3, 9, 5)) == [], "실패 뒤 10분 안에는 다시 시도하지 않는다(매 분 반복하지 않게)"
    (m,) = await tick(at(3, 9, 10)); assert has_sections(m) and list_alert.is_done(at(3, 9, 10))
    print("5) 전송 실패 재시도 OK")

    # Scenario 6: 발송하지 않는 시간 방식의 30분 규칙 — 06:00에 끝나는데 06:30까지 다운로드 리포트가 없으면 이 알림만 묶어서 보낸다. 그 뒤 리포트에는 안 얹는다
    reset([q(23, 0, 6, 0)])
    assert await tick(at(3, 6, 29)) == [], "30분이 안 지났다"
    (m,) = await tick(at(3, 6, 30)); assert m.startswith("🔔") and has_sections(m), m
    (m,) = await batch(at(3, 7, 0)); assert no_sections(m), "이미 나갔으니 그 뒤 리포트에는 없다"
    # 06:10에 다운로드 리포트가 나갔으면(얹힘) 06:30에 단독 발송은 없다
    reset([q(23, 0, 6, 0)]); (m,) = await batch(at(3, 6, 10)); assert has_sections(m); assert await tick(at(3, 6, 30)) == []
    # 발송하지 않는 시간 중에는 단독 발송도 미룬다(두 구간: 06:00과 08:00에 끝남 → 가장 빠른 06:00 기준인데, 06:10~08:00도 발송하지 않는 시간)
    reset([q(23, 0, 6, 0), q(6, 10, 8, 0)]); assert await tick(at(3, 7, 0)) == [], "06:10~08:00은 발송하지 않는 시간"
    (m,) = await tick(at(3, 8, 0)); assert has_sections(m), "끝나면 보낸다"
    print("6) 30분 규칙 OK")

    # Scenario 7: 그날 끝나는 구간이 없는 요일 — 그날 첫 리포트에 얹고 단독 발송은 없다(월요일 시작만 있는 구간)
    reset([q(23, 0, 6, 0, ["mon"])])     # 2026-11-02는 월요일, 11-03은 화요일(06:00에 끝남)
    assert await tick(at(2, 12, 0)) == []; (m,) = await batch(at(2, 9, 0)); assert has_sections(m), "월요일 첫 리포트에 얹힌다"
    print("7) 끝나는 구간 없는 요일 OK")

    # Scenario 8: 발송하지 않는 시간을 나중에 설정해도 먼저 정한 발송 시각을 유지하기로 했으면 그 시각에 단독 발송(다운로드 리포트에는 안 얹음)
    reset([q(23, 0, 6, 0)], time="18:30")
    (m,) = await batch(at(3, 7, 0)); assert no_sections(m); assert await tick(at(3, 18, 29)) == []; (m,) = await tick(at(3, 18, 30)); assert has_sections(m)
    print("8) 시각 유지 OK")

    # Scenario 9: 테스트 발송 버튼은 알린 것도 포함해 전부 보여 주고, 하루 한 번 기록(done)이나 "알렸다" 기록을 건드리지 않는다
    reset(time="09:00"); report_seen.remember_new([("12", "신규 네이버")], [])
    before = len(sent); job_status.start("report"); await scheduler.run_test_report()
    m = sent[before]; assert has_sections(m) and "신규 네이버" in m, "테스트 발송은 알린 신규도 보여 준다"
    assert not list_alert.is_done(at(3, 12, 0)) and report_seen.is_new_announced("naver", "12") and not report_seen.is_new_announced("kakao", 22), "기록을 남기지 않는다"
    print("9) 테스트 발송 OK")
    # Scenario 10: 아무것도 설정하지 않은 사용자(발송하지 않는 시간 없음, 섹션 옵션 켜짐, 디스코드 설정됨) — 기본 오전 7시에 한 번 따로 나간다
    reset(); assert await tick(at(3, 6, 59)) == []
    (m,) = await tick(at(3, 7, 0)); assert m.startswith("🔔") and has_sections(m); assert await tick(at(3, 7, 30)) == [] and no_sections((await batch(at(3, 12, 0)))[0]), "그날 한 번뿐이고 다운로드 리포트에는 안 얹힌다"
    print("10) 기본 오전 7시 OK")
asyncio.run(main())
print("ALL OK")
