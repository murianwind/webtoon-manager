"""성인 인증 쿠키 만료 판단(app/cookie_health.py) — 별도 호출 없이, 이번 다운로드에서 시도한 성인 웹툰들의 결과로 판단한다.
시도한 성인 웹툰이 전부 회차를 하나도 못 가져왔을 때만(전멸) 만료로 의심하고, 한 번 알리면 복구될 때까지 다시 알리지 않는다(복구되면 플래그를 지워 다음 만료 때 다시 1회).
"""
import asyncio

from app import cookie_health as ch, db, repository
db.get_connection()
from fakes import discord_fake

sent = []; ch.discord_notify.send_webhook_notification = discord_fake(sent)
KEY = ch._SETTING_KEY_EXPIRED_NOTIFIED
def tracker(*episode_counts):
    t = ch.AdultFetchTracker()
    for n in episode_counts: t.record(n)
    return t
finish = lambda t: asyncio.run(ch.finalize_and_notify(None, t))

# Scenario 1: 집계 — 시도 수와 성공(회차 1개 이상) 수. 전멸은 "시도했고 성공이 0"일 때만
t = ch.AdultFetchTracker(); assert t.attempted == 0 and not t.all_failed, "시도가 없으면 전멸이 아니다"
t.record(0); assert t.attempted == 1 and t.got_episodes == 0 and t.all_failed
t.record(3); assert t.attempted == 2 and t.got_episodes == 1 and not t.all_failed, "하나라도 성공하면 쿠키 문제가 아니다"
assert tracker(0, 0, 0).all_failed and not tracker(0, 0, 5).all_failed
print("1) 집계 OK")

# Scenario 2: 성인 웹툰을 하나도 안 건드렸으면 판단 근거가 없어 아무것도 안 한다(플래그도 그대로)
repository.set_setting(KEY, "1"); sent.clear(); finish(tracker()); assert sent == [] and repository.get_setting(KEY) == "1"
repository.set_setting(KEY, None)
print("2) 시도 없음 OK")

# Scenario 3: 전멸 — 처음이면 디스코드로 한 번 알리고(시도한 개수 포함) 플래그를 남긴다. 계속 전멸이어도 다시 알리지 않는다
sent.clear(); finish(tracker(0, 0, 0))
assert len(sent) == 1 and "🍪 **네이버 쿠키 만료 의심**" in sent[0] and "성인 인증이 필요한 웹툰 3개" in sent[0] and "쿠키 파일" in sent[0], sent
assert repository.get_setting(KEY) == "1"
finish(tracker(0, 0)); finish(tracker(0)); assert len(sent) == 1, "복구되기 전에는 다시 알리지 않는다"
print("3) 만료 알림 1회 OK")

# Scenario 4: 복구 — 이번에 하나라도 성공하면 플래그를 지운다(알림은 없음). 그 뒤 다시 전멸하면 다시 1회 알린다
sent.clear(); finish(tracker(0, 4)); assert sent == [] and repository.get_setting(KEY) is None
finish(tracker(0, 0)); assert len(sent) == 1 and repository.get_setting(KEY) == "1"
print("4) 복구/재알림 OK")

# Scenario 5: 정상 상태에서는(플래그 없음) 아무 일도 없다 — 알림도 플래그도 없다
repository.set_setting(KEY, None); sent.clear(); finish(tracker(2, 3)); assert sent == [] and repository.get_setting(KEY) is None
print("ALL OK")
