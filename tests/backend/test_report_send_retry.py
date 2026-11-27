"""다운로드 리포트 전송이 실패했을 때(웹훅이 False를 돌려주거나 예외) — 묶음을 끝내지 않고 10분 간격으로 다시 시도한다. 새 다운로드가 없어도 성공할 때까지(최대 6번).
- 발송 기준 시각/알렸다는 기록은 성공했을 때만 움직인다. 포기하면 이력은 다음 리포트에 같이 담긴다.
- 보낸 뒤에 생긴 오류(기록 저장 등)는 이미 나간 메시지를 또 보내지 않게 다시 시도하지 않는다."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db, discord_config, discord_notify, list_alert, report_dispatch as rd, report_seen, repository, scheduler, viewer_scan
list_alert.should_attach = lambda now: False
async def no_titles(session, settings): return []
for name in ("collect_naver_new_titles", "collect_kakao_new_titles", "collect_unregistered_new_episodes", "collect_kakao_new_episodes"): setattr(scheduler.report_sections, name, no_titles)
db.get_connection()

KST, UTC = ZoneInfo("Asia/Seoul"), timezone.utc
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=KST)
MIN = lambda n: timedelta(minutes=n)
LAST_SENT = "report_last_sent_at"
at = lambda d, h, mi: datetime(2026, 10, d, h, mi, tzinfo=KST)

sent = []; calls = [0]; MODE = {"v": "fail"}        # fail: False를 돌려줌 / raise: 예외 / ok: 성공
async def fake_send(session, message):
    calls[0] += 1
    if MODE["v"] == "raise": raise RuntimeError("웹훅 오류")
    if MODE["v"] == "fail": return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send
discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")

def reset(*, quiet=()):
    sent.clear(); calls[0] = 0; MODE["v"] = "fail"
    with db.write_transaction() as cx: cx.execute("DELETE FROM episode_history")
    for key in (rd.STATE_KEY, viewer_scan.STATE_KEY, report_seen.SETTING_KEY): repository.set_setting(key, None)
    repository.set_setting(LAST_SENT, (T0 - timedelta(days=1)).astimezone(UTC).isoformat())
    rd.set_quiet_hours(list(quiet)); rd.set_batch_minutes(0); discord_config.set_webhook_url("https://discord.com/api/webhooks/1/test")
_seq = [0]
def download(name, when):
    _seq[0] += 1
    repository.add_episode_history(f"t{_seq[0]}", name, _seq[0], f"{_seq[0]}화", "success", platform="naver")
    with db.write_transaction() as cx: cx.execute("UPDATE episode_history SET downloaded_at = ? WHERE id = (SELECT MAX(id) FROM episode_history)", (when.astimezone(UTC).isoformat(),))
    rd.note_download_finished(when)
def tick(now): asyncio.run(scheduler.run_report_tick(now))
state = rd.load_state
last_sent = lambda: repository.get_setting(LAST_SENT)

# 1. 실패하면 묶음을 끝내지 않는다 — 10분이 지나기 전에는 다시 보내지 않고, 10분 뒤 새 다운로드가 없어도 다시 시도해서 성공하면 끝난다
for mode in ("fail", "raise"):
    reset(); download("작품A", T0); MODE["v"] = mode
    tick(T0); assert calls[0] == 1 and sent == [] and state()["cutoff"] is not None, f"{mode}: 실패해도 묶음은 남는다"
    assert last_sent() == (T0 - timedelta(days=1)).astimezone(UTC).isoformat(), "발송 기준 시각은 그대로"
    tick(T0 + MIN(5)); tick(T0 + MIN(9)); assert calls[0] == 1, "10분 전에는 다시 보내지 않는다"
    MODE["v"] = "ok"; tick(T0 + MIN(10)); assert len(sent) == 1 and "작품A" in sent[0] and calls[0] == 2, sent
    assert state()["cutoff"] is None and last_sent() == T0.astimezone(UTC).isoformat(), "성공하면 정리하고 기준 시각을 옮긴다"
    tick(T0 + MIN(30)); assert len(sent) == 1, "성공 뒤에는 또 보내지 않는다"
print("1) 실패 후 10분 간격 재시도 OK")

# 2. 계속 실패하면 6번까지만 시도하고 포기한다 — 묶음은 정리하되 이력은 남아서 다음 리포트에 같이 담긴다
reset(); download("포기작", T0)
for i in range(12): tick(T0 + MIN(10 * i))
assert calls[0] == rd.MAX_SEND_ATTEMPTS == 6 and state()["cutoff"] is None and state()["last_download_at"] is None, (calls[0], state())
assert last_sent() == (T0 - timedelta(days=1)).astimezone(UTC).isoformat()
MODE["v"] = "ok"; download("새작품", T0 + MIN(200)); tick(T0 + MIN(201)); assert len(sent) == 1 and "포기작" in sent[0] and "새작품" in sent[0], sent
print("2) 6번 실패 후 포기, 이력은 다음 리포트에 OK")

# 3. 기다리는 동안 새 다운로드가 끝나면 묶음이 다시 열리고, 실패 횟수도 새로 센다 — 이전 이력과 함께 한 통으로
reset(); download("먼저", T0); tick(T0); tick(T0 + MIN(10)); assert calls[0] == 2
download("나중", T0 + MIN(12)); assert state()["cutoff"] is None and state()["send_failures"] == 0 and state()["send_retry_at"] is None, state()
MODE["v"] = "ok"; tick(T0 + MIN(13)); assert len(sent) == 1 and "먼저" in sent[0] and "나중" in sent[0], sent
print("3) 기다리는 중 새 다운로드 OK")

# 4. 발송하지 않는 시간에는 재시도 시각이 지나도 보내지 않고 끝난 뒤에 보낸다
quiet = {"start_hour": 22, "start_minute": 0, "end_hour": 6, "end_minute": 0, "days": []}
reset(quiet=[quiet]); download("밤작품", at(13, 21, 50)); tick(at(13, 21, 55)); assert calls[0] == 1
MODE["v"] = "ok"; tick(at(13, 22, 30)); tick(at(14, 3, 0)); assert sent == [] and calls[0] == 1, "발송하지 않는 시간에는 재시도도 안 한다"
tick(at(14, 6, 0)); assert len(sent) == 1 and "밤작품" in sent[0]
print("4) 발송하지 않는 시간 OK")

# 5. 기다리는 동안 웹훅이 지워지면 보낼 곳이 없으니 묶음을 정리한다(더 시도하지 않는다)
reset(); download("작품", T0); tick(T0); assert calls[0] == 1
discord_config.set_webhook_url(None); repository.set_setting(discord_config._KEY_WEBHOOK_URL, None); tick(T0 + MIN(10)); assert calls[0] == 1 and state()["cutoff"] is None
print("5) 웹훅 삭제 OK")

# 6. 보낸 뒤의 오류(기록 저장 실패 등)는 이미 나간 메시지를 또 보내지 않도록 다시 시도하지 않는다
reset(); download("작품", T0); MODE["v"] = "ok"
real_remember = report_seen.remember_rows
def boom(rows): raise RuntimeError("기록 저장 실패")
report_seen.remember_rows = boom
tick(T0); report_seen.remember_rows = real_remember
assert len(sent) == 1 and state()["cutoff"] is None, "이미 나갔으니 묶음은 정리한다"
tick(T0 + MIN(10)); tick(T0 + MIN(20)); assert len(sent) == 1, "같은 메시지를 다시 보내지 않는다"
print("6) 보낸 뒤 오류는 재시도 안 함 OK")

# 7. 재시작해도 이어진다 — 재시도 시각/횟수는 저장된 상태에 있다
reset(); download("작품", T0); tick(T0); saved = state(); assert saved["send_failures"] == 1 and saved["send_retry_at"] == (T0 + MIN(10)).astimezone(UTC).isoformat(), saved
print("7) 상태 저장 OK")
print("\n전부 통과")
