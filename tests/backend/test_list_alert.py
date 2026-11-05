"""웹툰 전체목록 알림(신규 웹툰 + 새 에피소드)의 "언제 나가나" 규칙 — app/list_alert.py. 하루(한국시간) 한 번만 나간다.
- 새 에피소드 발송 시각이 설정돼 있으면(명시) 그 시각에 이 알림만 단독으로 나간다(다운로드 리포트에는 안 얹는다).
- 시각 설정이 없고 발송하지 않는 시간이 있으면 → 그날 발송하지 않는 시간이 끝나는 시각 중 가장 빠른 시각 이후 처음 나가는 다운로드 리포트에 얹고, 그 시각 뒤 30분 안에 다운로드 리포트가 안 나가면 단독으로 보낸다.
  (그날 끝나는 발송하지 않는 시간이 없는 요일이면 그날 첫 리포트에 얹고 단독 발송은 안 한다.)
- 둘 다 없으면(예전부터 쓰던 사용자: 발송하지 않는 시간을 안 정했고 섹션 옵션이 켜져 있음) 기본 시각(오전 7시)에 단독으로. 디스코드가 설정돼 있지 않거나 "미등록 웹툰 새 에피소드 섹션" 옵션이 꺼져 있으면 나가지 않는다. 발송하지 않는 시간에는 단독 발송도 미룬다.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import db, discord_config, list_alert, report_dispatch, repository
db.get_connection()

KST = ZoneInfo("Asia/Seoul")
def at(y, mo, d, h, mi=0): return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(timezone.utc)
MON, TUE = (2026, 11, 2), (2026, 11, 3)          # 2026-11-02는 월요일
q = lambda sh, sm, eh, em, days=(): {"start_hour": sh, "start_minute": sm, "end_hour": eh, "end_minute": em, "days": list(days)}

def reset(quiet=(), time=None, webhook="https://discord.test/hook", enabled=True):
    repository.set_setting(list_alert.SETTING_DONE, None); repository.set_setting(list_alert.SETTING_TIME, None)
    report_dispatch.set_quiet_hours(list(quiet))
    if webhook: discord_config.set_webhook_url(webhook)
    else: repository.set_setting("discord_webhook_url", None)                                                        # 빈 값으로는 지워지지 않아서 직접 지운다
    repository.set_setting("report_unregistered_new_episodes_enabled", "1" if enabled else "0")
    if time: list_alert.set_time(time)

# Scenario 1: 그날(요일) 끝나는 발송하지 않는 시간의 종료 시각(분) — 자정을 넘기는 구간은 시작한 다음 날 끝난다, 요일 지정은 시작하는 날 기준, 여러 개면 모두
reset([q(23, 0, 6, 0)]); assert [report_dispatch.quiet_end_minutes(w) for w in range(7)] == [[360]] * 7, "매일 23:00~06:00 → 매일 06:00에 끝난다"
reset([q(23, 0, 6, 0, ["mon"])]); assert report_dispatch.quiet_end_minutes(1) == [360] and all(report_dispatch.quiet_end_minutes(w) == [] for w in (0, 2, 3, 4, 5, 6)), "월요일 23:00 시작은 화요일 06:00에 끝난다"
reset([q(23, 0, 6, 0, ["sun"])]); assert report_dispatch.quiet_end_minutes(0) == [360] and report_dispatch.quiet_end_minutes(6) == [], "일요일 시작은 월요일에 끝난다(주를 넘김)"
reset([q(22, 0, 7, 0), q(12, 0, 13, 0)]); assert report_dispatch.quiet_end_minutes(3) == [420, 780], "하루에 두 번 끝나면 둘 다(작은 것부터)"
reset([q(9, 0, 10, 0, ["wed"])]); assert report_dispatch.quiet_end_minutes(2) == [600], "같은 날 안에서 끝나는 구간"
print("1) 종료 시각 계산 OK")

# Scenario 2: 시각 설정 — "HH:MM"만(0:00~23:59), 지우면 None. 명시한 값만 get_time이 돌려주고 기본값은 따로(DEFAULT_TIME)
reset(); assert list_alert.get_time() is None and list_alert.DEFAULT_TIME == "07:00"
list_alert.set_time("18:30"); assert list_alert.get_time() == "18:30"
list_alert.set_time("9:05"); assert list_alert.get_time() == "09:05", "한 자리 시도 맞춰 저장"
list_alert.set_time(None); assert list_alert.get_time() is None
for bad in ("25:00", "12:60", "abc", "", "12", "12:5x"):
    try: list_alert.set_time(bad)
    except ValueError: continue
    raise AssertionError(f"잘못된 시각을 받았다: {bad!r}")
print("2) 시각 설정 OK")

# Scenario 3: 어떤 방식인가 — 시각을 명시했으면 그것(발송하지 않는 시간이 있어도 유지), 아니면 발송하지 않는 시간 기준, 둘 다 없으면 기본 시각
reset(); p = list_alert.plan(at(*MON, 12)); assert (p.mode, p.anchor_minute) == ("time", 420), p            # 기본 오전 7시
reset(time="18:30"); p = list_alert.plan(at(*MON, 12)); assert (p.mode, p.anchor_minute) == ("time", 1110)
reset([q(23, 0, 6, 0)]); p = list_alert.plan(at(*MON, 12)); assert (p.mode, p.anchor_minute) == ("quiet", 360)
reset([q(23, 0, 6, 0)], time="18:30"); p = list_alert.plan(at(*MON, 12)); assert (p.mode, p.anchor_minute) == ("time", 1110), "먼저 설정한 시각을 유지하기로 한 경우"
reset([q(22, 0, 7, 0), q(12, 0, 13, 0)]); assert list_alert.plan(at(*MON, 9)).anchor_minute == 420, "여러 개면 그날 끝나는 시각 중 가장 빠른 것"
reset([q(23, 0, 6, 0, ["mon"])]); p = list_alert.plan(at(*MON, 12)); assert (p.mode, p.anchor_minute) == ("quiet", None), "그날 끝나는 구간이 없다(월요일은 시작만)"
# 아무것도 설정하지 않은 사용자(발송하지 않는 시간 없음 + 섹션 옵션 켜짐) — 기본 오전 7시부터 단독 발송 대상
reset(); assert not list_alert.standalone_due(at(*MON, 6, 59)) and list_alert.standalone_due(at(*MON, 7, 0)), "기본 시각은 오전 7시"
print("3) 방식 결정 OK")

# Scenario 4: 시각 방식 — 그 시각부터 단독 발송 대상(하루 한 번), 다운로드 리포트에는 얹지 않는다
reset(time="09:00")
assert not list_alert.standalone_due(at(*MON, 8, 59)) and list_alert.standalone_due(at(*MON, 9, 0)) and list_alert.standalone_due(at(*MON, 23, 0)), "그 시각부터(놓쳤으면 그날 안에 늦게라도)"
assert not list_alert.should_attach(at(*MON, 12)), "시각 방식에서는 다운로드 리포트에 얹지 않는다"
list_alert.mark_done(at(*MON, 9, 1)); assert list_alert.is_done(at(*MON, 20)) and not list_alert.standalone_due(at(*MON, 20)), "그날은 한 번만"
assert not list_alert.is_done(at(*TUE, 0, 5)) and list_alert.standalone_due(at(*TUE, 9, 0)), "다음 날(한국시간 자정 이후)은 다시"
print("4) 시각 방식 OK")

# Scenario 5: 발송하지 않는 시간 방식 — 끝나는 시각(06:00) 이후 처음 나가는 다운로드 리포트에 얹고, 30분 안에 안 나가면 06:30부터 단독 발송 대상
reset([q(23, 0, 6, 0)])
assert not list_alert.should_attach(at(*MON, 5, 59)) and list_alert.should_attach(at(*MON, 6, 0)) and list_alert.should_attach(at(*MON, 15, 0)), "끝난 뒤의 리포트에 얹는다(끝나기 전에는 아님)"
assert not list_alert.standalone_due(at(*MON, 6, 29)) and list_alert.standalone_due(at(*MON, 6, 30)), "끝난 뒤 30분이 지나도 리포트가 안 나가면 단독"
list_alert.mark_done(at(*MON, 6, 10)); assert not list_alert.should_attach(at(*MON, 7, 0)) and not list_alert.standalone_due(at(*MON, 7, 0)), "얹어서 나갔으면 그날 끝"
print("5) 발송하지 않는 시간 방식 OK")

# Scenario 6: 그날 끝나는 구간이 없는 날 — 그날 첫 리포트에 얹고(단독 발송은 없다)
reset([q(23, 0, 6, 0, ["mon"])]); assert list_alert.should_attach(at(*MON, 10, 0)) and not list_alert.standalone_due(at(*MON, 20, 0)), "월요일: 끝나는 구간 없음 → 얹기만(23시는 발송하지 않는 시간이라 20시로 본다)"
assert not list_alert.should_attach(at(*TUE, 5, 0)) and list_alert.should_attach(at(*TUE, 6, 0)), "화요일은 06:00에 끝난다"
print("6) 끝나는 구간 없는 날 OK")

# Scenario 7: 나가지 않는 조건 — 디스코드 미설정, 섹션 옵션 꺼짐, 발송하지 않는 시간 중(단독 발송은 미룬다)
reset(time="09:00", webhook=""); assert not list_alert.standalone_due(at(*MON, 12)), "디스코드 미설정"
reset(time="09:00", enabled=False); assert not list_alert.standalone_due(at(*MON, 12)), "섹션 옵션 꺼짐(시각 방식)"
reset([q(23, 0, 6, 0)], enabled=False); assert not list_alert.should_attach(at(*MON, 12)) and not list_alert.standalone_due(at(*MON, 12)), "섹션 옵션 꺼짐(발송하지 않는 시간 방식)"
reset([q(8, 0, 11, 0)], time="09:00"); assert not list_alert.standalone_due(at(*MON, 9, 30)) and list_alert.standalone_due(at(*MON, 11, 0)), "발송하지 않는 시간 중에는 미루고 끝나면 보낸다"
print("7) 나가지 않는 조건 OK")
print("ALL OK")
