"""회차가 늘지 않는 카카오 웹툰 알림 — 구독 중이고 연재 중("Y")인데 회차 수가 N일(설정, 기본 30, 0이면 끄기) 이상 그대로인 작품을 알린다.
- 추적: 다운로드 때마다 회차 수를 기록하고, 회차가 늘거나 연재 중이 아니게(휴재/완결) 되면 기준일을 오늘로 되돌린다. 기능이 꺼져 있으면 기록하지 않고, 켜면 처음부터 센다.
- 언제: 하루(한국시간) 한 번 — 발송하지 않는 시간이 있으면 그날 가장 빠른 종료 시각 이후 첫 다운로드 리포트에, 없으면 그날 첫 리포트에 얹는다. 같은 정체는 한 번만 알린다(회차가 늘면 다시 센다).
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import db, discord_config, report_dispatch, report_seen, repository, stalled_episodes
db.get_connection()
import app.scheduler as scheduler
from app.scheduler import report_message

KST = ZoneInfo("Asia/Seoul")
def at(month, day, h=9, mi=0): return datetime(2026, month, day, h, mi, tzinfo=KST).astimezone(timezone.utc)
q = lambda sh, sm, eh, em: {"start_hour": sh, "start_minute": sm, "end_hour": eh, "end_minute": em, "days": []}
row = lambda tid: repository.get_kakao_webtoon(tid)

def add(tid, title, status=None):
    repository.upsert_new_kakao_webtoon(tid, title, status=status or repository.STATUS_ACTIVE)

def tracking():
    # Scenario 1: 설정 — 기본 30일, 0~365만 저장, 잘못된 값은 오류
    assert stalled_episodes.get_days() == 30
    stalled_episodes.set_days(45); assert stalled_episodes.get_days() == 45
    for bad in (-1, 366):
        try: stalled_episodes.set_days(bad); assert False, bad
        except ValueError as e: assert "0~365" in str(e)
    assert stalled_episodes.get_days() == 45
    stalled_episodes.set_days(365); assert stalled_episodes.get_days() == 365   # 경계값은 허용
    stalled_episodes.set_days(30)

    # Scenario 2: 처음 보면 오늘을 기준일로 기록하고, 같은 회차 수면 기준일을 유지한다. 회차가 늘면 기준일을 오늘로 옮기고 알림 기록을 지운다
    add(1, "정체작")
    stalled_episodes.track(1, 100, True, at(10, 1))
    assert (row(1)["episode_count_seen"], row(1)["episode_count_since"], row(1)["stall_notified"]) == (100, "2026-10-01", False)
    stalled_episodes.track(1, 100, True, at(10, 20)); assert row(1)["episode_count_since"] == "2026-10-01"
    stalled_episodes.mark_notified([1]); assert row(1)["stall_notified"] is True
    stalled_episodes.track(1, 101, True, at(11, 5))
    assert (row(1)["episode_count_seen"], row(1)["episode_count_since"], row(1)["stall_notified"]) == (101, "2026-11-05", False)

    # Scenario 3: 연재 중이 아니면(휴재 P/완결 N) 기준일을 계속 오늘로 되돌린다 — 쉬는 작품은 정체로 세지 않고, 연재로 돌아온 날부터 센다
    stalled_episodes.track(1, 101, False, at(11, 10)); stalled_episodes.track(1, 101, False, at(11, 20))
    assert row(1)["episode_count_since"] == "2026-11-20"
    stalled_episodes.track(1, 101, True, at(11, 25)); assert row(1)["episode_count_since"] == "2026-11-20"

    # Scenario 4: 회차 수를 모르면(None) 아무것도 바꾸지 않는다
    stalled_episodes.track(1, None, True, at(12, 1)); assert row(1)["episode_count_since"] == "2026-11-20" and row(1)["episode_count_seen"] == 101

    # Scenario 5: 끄면(0) 기록하지 않는다. 다시 켜면 이전 기록을 지우고 처음부터 센다
    add(2, "꺼진동안")
    stalled_episodes.set_days(0)
    stalled_episodes.track(2, 50, True, at(10, 1)); assert row(2)["episode_count_seen"] is None
    stalled_episodes.track(1, 999, True, at(12, 1)); assert row(1)["episode_count_seen"] == 101, "꺼진 동안은 기존 기록도 건드리지 않는다"
    stalled_episodes.set_days(30)
    assert row(1)["episode_count_seen"] is None and row(1)["episode_count_since"] is None, "켜면 이전 기록 초기화"
    stalled_episodes.set_days(30); stalled_episodes.track(1, 7, True, at(12, 2)); stalled_episodes.set_days(45)
    assert row(1)["episode_count_seen"] == 7, "켜진 상태에서 값만 바꾸면 초기화하지 않는다"
    stalled_episodes.set_days(30)
    print("1) 설정/추적 OK")

def seed():
    """대상 고르기 시나리오의 작품들 — 10: 30일째(경계), 11: 29일째, 12: 구독해제, 13: 이미 알림, 14: 제외됨."""
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = NULL, episode_count_since = NULL, stall_notified = 0")
    for tid, title, status in ((10, "삼십일째", None), (11, "이십구일째", None), (12, "구독해제", repository.STATUS_UNSUBSCRIBED), (13, "이미알림", None), (14, "제외됨", repository.STATUS_EXCLUDED)):
        add(tid, title, status)
    for tid, since in ((10, "2026-10-31"), (11, "2026-11-01"), (12, "2026-09-01"), (13, "2026-09-01"), (14, "2026-09-01")):
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = 77, episode_count_since = ?, stall_notified = ? WHERE title_id = ?", (since, 1 if tid == 13 else 0, tid))

def due():
    seed(); now = at(11, 30)
    # Scenario 6: N일 이상(경계 포함) 그대로인 구독 중 작품만, 이미 알린 것/구독해제/제외됨은 뺀다. 오래 정체된 순
    got = stalled_episodes.due(now)
    assert [(d["title_id"], d["title"], d["count"], d["days"]) for d in got] == [(10, "삼십일째", 77, 30)], got
    stalled_episodes.set_days(10); assert [d["title_id"] for d in stalled_episodes.due(now)] == [10, 11]; stalled_episodes.set_days(30)
    stalled_episodes.set_days(0); assert stalled_episodes.due(now) == []; stalled_episodes.set_days(30)  # (다시 켜면 기록이 지워진다 — flow에서 seed를 다시 넣는다)
    print("2) 대상 고르기 OK")

def timing():
    # Scenario 7: 발송하지 않는 시간이 없으면 하루 첫 리포트(언제든), 알린 뒤엔 그날 더 없고 다음 날 다시
    report_dispatch.set_quiet_hours([]); repository.set_setting(stalled_episodes.SETTING_DONE, None)
    assert stalled_episodes.should_attach(at(11, 30, 0, 5)) is True
    stalled_episodes.mark_done(at(11, 30, 1, 0)); assert stalled_episodes.should_attach(at(11, 30, 15)) is False and stalled_episodes.should_attach(at(12, 1, 0, 1)) is True
    # Scenario 8: 발송하지 않는 시간(23~06시)이 있으면 끝난 시각(06:00) 이후부터
    report_dispatch.set_quiet_hours([q(23, 0, 6, 0)]); repository.set_setting(stalled_episodes.SETTING_DONE, None)
    assert stalled_episodes.should_attach(at(11, 30, 5, 59)) is False and stalled_episodes.should_attach(at(11, 30, 6, 0)) is True
    stalled_episodes.mark_done(at(11, 30, 7, 0)); assert stalled_episodes.should_attach(at(11, 30, 8)) is False
    # Scenario 9: 끄면(0) 얹지 않는다
    repository.set_setting(stalled_episodes.SETTING_DONE, None); stalled_episodes.set_days(0)
    assert stalled_episodes.should_attach(at(11, 30, 9)) is False; stalled_episodes.set_days(30)
    report_dispatch.set_quiet_hours([])
    print("3) 하루 한 번 타이밍 OK")

def message():
    lines = report_message._stalled_lines([{"title_id": 10, "title": "삼십일째", "count": 77, "days": 31}])
    assert lines == ["• [카카오] 삼십일째 · 77화 · 31일째 그대로 [바로가기](<https://page.kakao.com/content/10>)"], lines
    full = report_message.build_report_message([{"status": "success", "title_name": "받은", "episode_no": 1, "title_id": "1", "platform": "naver"}], [], {}, stalled=[{"title_id": 10, "title": "삼십일째", "count": 77, "days": 31}])
    assert "회차가 늘지 않는 카카오 웹툰 (1)" in full and "삼십일째" in full
    assert "회차가 늘지 않는" not in report_message.build_report_message([], [], {}, stalled=[])
    print("4) 메시지 OK")

sent = []
async def fake_send(session, text): sent.append(text)
scheduler.report_job.discord_notify.send_webhook_notification = fake_send
rs = scheduler.report_sections
async def _empty(session, settings): return []
rs.collect_naver_new_titles = rs.collect_kakao_new_titles = rs.collect_unregistered_new_episodes = rs.collect_kakao_new_episodes = _empty
counter = [0]
async def batch(now):
    counter[0] += 1
    repository.add_episode_history("90000", f"받은{counter[0]}", counter[0], f"{counter[0]}화", "success", platform="naver")
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE title_name = ?", (now.isoformat(), f"받은{counter[0]}"))
    before = len(sent); await scheduler.send_batch_report(now.isoformat(), now=now); return sent[before:]

async def flow():
    seed()
    repository.set_setting("kakao_webtoons_enabled", "1"); discord_config.set_webhook_url("https://discord.test/hook")
    repository.set_setting(scheduler.report_job._SETTING_KEY_REPORT_LAST_SENT_AT, at(11, 1).isoformat())
    for key in (stalled_episodes.SETTING_DONE, report_seen.SETTING_KEY): repository.set_setting(key, None)
    now = at(11, 30, 9)
    # Scenario 10: 리포트에 얹히고(정체 작품 이름 포함), 그 작품은 알림 완료로 기록되며, 같은 날 다음 리포트에는 없다
    (m,) = await batch(now); assert "회차가 늘지 않는 카카오 웹툰 (1)" in m and "삼십일째" in m, m
    assert row(10)["stall_notified"] is True and stalled_episodes.is_done(now)
    (m,) = await batch(at(11, 30, 12)); assert "회차가 늘지 않는" not in m, "하루 한 번"
    # Scenario 11: 다음 날 새로 정체가 된 작품이 생기면 그 작품만 알린다(이미 알린 작품은 반복 안 함)
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = 5, episode_count_since = '2026-10-01', stall_notified = 0 WHERE title_id = 11")
    (m,) = await batch(at(12, 1, 9)); assert "이십구일째" in m and "삼십일째" not in m, m
    # Scenario 11-2: 하루 한 번 — 같은 날 두 번째 리포트에는 새로 정체가 된 작품이 있어도 안 얹는다(다음 날 알린다)
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = 5, episode_count_since = '2026-10-01', stall_notified = 0 WHERE title_id = 10")
    (m,) = await batch(at(12, 1, 15)); assert "회차가 늘지 않는" not in m, m
    (m,) = await batch(at(12, 2, 9)); assert "삼십일째" in m, "다음 날 알린다"
    # Scenario 11-3: 발송하지 않는 시간(23~06시)이 끝나기 전의 리포트에는 안 얹고, 끝난 뒤 첫 리포트에 얹는다
    report_dispatch.set_quiet_hours([q(23, 0, 6, 0)])
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = 5, episode_count_since = '2026-10-01', stall_notified = 0 WHERE title_id = 11")
    (m,) = await batch(at(12, 3, 5, 30)); assert "회차가 늘지 않는" not in m and not stalled_episodes.is_done(at(12, 3, 5, 30)), m
    (m,) = await batch(at(12, 3, 7, 0)); assert "이십구일째" in m, m
    report_dispatch.set_quiet_hours([])
    # Scenario 12: 전송이 실패하면 알림 기록도, 오늘 처리도 하지 않아 다음에 다시 얹힌다
    with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET episode_count_seen = 5, episode_count_since = '2026-10-01', stall_notified = 0 WHERE title_id = 13")
    async def failing(session, text): raise RuntimeError("웹훅 실패")
    scheduler.report_job.discord_notify.send_webhook_notification = failing
    await batch(at(12, 2, 9)); assert row(13)["stall_notified"] is False and not stalled_episodes.is_done(at(12, 2, 9))
    scheduler.report_job.discord_notify.send_webhook_notification = fake_send
    # Scenario 13: 테스트 발송 버튼은 대상이 있으면 보여 주되 알림 기록은 건드리지 않는다(받은 이력이 없어도 정체 작품만으로 보낸다)
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
    before = len(sent); await scheduler.run_test_report(); assert len(sent) == before + 1 and "회차가 늘지 않는 카카오 웹툰" in sent[-1] and row(13)["stall_notified"] is False
    print("5) 리포트 연동 OK")

tracking(); due(); timing(); message(); asyncio.run(flow())
print("ALL OK")
