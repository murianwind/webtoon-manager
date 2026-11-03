"""아카이빙 작업(scheduler/archive_job.py) — 정기 아카이빙 + 완결 대기열을 돌리고, 문제(이름 충돌/이동 실패)를 모아서 한 번에 알리며, 뷰어 서버가 있으면 "읽은 회차만 옮긴다".
실제 파일 이동(archiver)과 rclone 업데이트는 가짜로 바꾸고, 이 작업이 "무엇을 부르고/실패를 어떻게 다루고/무엇을 알리는지"를 확인한다.
"""
import asyncio

from app import archiver, db, discord_notify, job_status, rclone_updater, repository, viewer_scan, webtoon_server_client
db.get_connection()
import app.scheduler.archive_job as aj
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
from fakes import discord_fake

seen = {}; state = {"update": "rclone 최신", "update_fail": False, "periodic_fail": False, "periodic": 2, "pending": 1}
def reset():
    seen.clear(); state.update(update="rclone 최신", update_fail=False, periodic_fail=False, periodic=2, pending=1)
    repository.set_setting("webtoon_server_url", None); repository.set_setting("archive_history_retention_days", None)

async def fake_update():
    if state["update_fail"]: raise RuntimeError("깃허브 접속 실패")
    return state["update"]
def fake_periodic(archive_root, download_root, rclone_config, log_fn, conflicts, failures, kakao_root, provider):
    seen["periodic"] = {"provider": provider, "kakao_root": kakao_root}
    log_fn("정기 로그 한 줄")
    if state["periodic_fail"]: raise RuntimeError("디스크 오류")
    if seen.get("with_issues"):                                                                                                     # 문제가 있는 실행만 충돌/실패를 만든다
        conflicts.append(("충돌작품", "001.zip", "rename")); failures.append(("실패작품", "002.zip", "권한 없음"))
    return state["periodic"]
def fake_pending(archive_root, download_root, rclone_config, log_fn, conflicts, failures, kakao_root):
    seen["pending"] = True; return state["pending"]
rclone_updater.check_and_update = fake_update
archiver.run_periodic_archive, archiver.process_pending_finish_archives = fake_periodic, fake_pending
sent = []; discord_notify.send_webhook_notification = discord_fake(sent)
noted = []; viewer_scan.note_library_changed = lambda count, when=None: noted.append(count)
run = lambda: asyncio.run(aj.run_archive_job())
snap = lambda: job_status.snapshot()["archive"]
text = lambda: "\n".join(snap()["log"])

# Scenario 1: 정상 실행 — rclone 업데이트 확인 → 정기 아카이빙 → 완결 대기열 → 이력 정리, 성공으로 끝난다. 옮긴 파일 수를 로그에 남기고 뷰어 스캔 요청에 합계를 넘긴다(옮긴 게 있을 때만 요청되도록)
reset(); noted.clear(); sent.clear(); run()
assert snap()["status"] == "success" and "rclone 최신" in text() and "지정 웹툰 2개 파일 이동 완료" in text() and "완결 구독해제 대기열 1개 파일 이동 완료" in text(), text()
assert seen["periodic"]["provider"] is None, "뷰어 서버가 없으면 읽은 회차로 거르지 않는다"
assert noted == [3] and sent == [], (noted, sent)
print("1) 정상 실행 OK")

# Scenario 2: rclone 업데이트 확인이 실패해도 무시하고 계속한다(오류 문구만 로그에)
reset(); noted.clear(); state["update_fail"] = True; run()
assert snap()["status"] == "success" and "rclone 업데이트 확인 중 오류(무시하고 계속): 깃허브 접속 실패" in text() and "지정 웹툰 2개" in text()
print("2) 업데이트 확인 실패 무시 OK")

# Scenario 3: 아카이빙 자체가 예외로 실패하면 "오류"로 끝나고 오류 문구를 남긴다(뒤 단계는 안 돈다)
reset(); noted.clear(); state["periodic_fail"] = True; seen.pop("pending", None); run()
assert snap()["status"] == "error" and "오류: 디스크 오류" in text() and "pending" not in seen and noted == [], (text(), noted)
print("3) 아카이빙 실패 OK")

# Scenario 4: 뷰어 서버 주소가 있으면 "읽은 회차" 조회 함수를 넘긴다(작품 이름은 폴더 이름 규칙으로 바꿔서 조회, 카카오는 카카오 규칙) — 없으면 None
reset(); repository.set_setting("webtoon_server_url", "http://viewer")
lookups = []
async def fake_read(session, base, folder_name, timeout): lookups.append((base, folder_name)); return frozenset({1, 2})
webtoon_server_client.fetch_read_numbers = fake_read
async def run_with_provider():
    # 별도 스레드에서 돌던 동기 코드가 provider를 부르는 상황을 그대로 흉내 낸다
    def periodic(archive_root, download_root, rclone_config, log_fn, conflicts, failures, kakao_root, provider):
        seen["provider_result"] = (provider("작품: 제목?", False), provider("작품: 제목?", True)); return 0
    archiver.run_periodic_archive = periodic
    await aj.run_archive_job()
asyncio.run(run_with_provider())
assert seen["provider_result"] == (frozenset({1, 2}), frozenset({1, 2})) and len(lookups) == 2 and all(b == "http://viewer" for b, _ in lookups), lookups
assert lookups[0][1] == remove_forbidden_str("작품: 제목?") and lookups[1][1] == remove_forbidden_str_kakao("작품: 제목?"), lookups   # 폴더를 만들 때와 같은 이름 규칙(네이버/카카오)
archiver.run_periodic_archive = fake_periodic
print("4) 뷰어 읽은 회차 조회 OK")

# Scenario 5: 이름 충돌/이동 실패는 모아서 디스코드로 한 번에 알린다(건마다 알리면 스팸). 문제가 없으면 알리지 않는다
reset(); sent.clear(); seen["with_issues"] = True; run()
assert len(sent) == 1 and "📦 **아카이빙 실행 알림**" in sent[0] and "⚠️ 파일명 충돌 1건" in sent[0] and "- 충돌작품: 001.zip (이름 바꿔 저장)" in sent[0] and "❌ 이동 실패 1건" in sent[0] and "- 실패작품: 002.zip — 권한 없음" in sent[0], sent
print("5) 문제 모아서 알림 OK")
msg = []
async def collect(session, text): msg.append(text)
discord_notify.send_webhook_notification = collect
asyncio.run(aj._notify_archive_issues(None, [("a", "1.zip", "skip"), ("b", "2.zip", "overwrite"), ("c", "3.zip", "새정책")], []))
assert "(건너뜀)" in msg[0] and "(덮어씀)" in msg[0] and "(새정책)" in msg[0] and "이동 실패" not in msg[0], msg                      # 정책 이름은 한글로, 모르는 정책은 그대로
discord_notify.send_webhook_notification = discord_fake(sent)

# Scenario 6: 겹치는 실행은 건너뛴다(시간 제한 안에 끝나야 한다 — 건너뛰지 않으면 같은 락을 기다리며 멈춘다)
async def overlap():
    async with aj._archive_job_lock: await aj.run_archive_job()
reset(); seen.pop("periodic", None); asyncio.run(asyncio.wait_for(overlap(), 3)); assert "periodic" not in seen and "이미 실행 중이라 건너뜁니다" in text()
print("6) 중복 실행 방지 OK")

# Scenario 7: 이력 보관 기간 — 설정 없음/0 이하는 아무것도 안 지우고, 양수면 그 일수로 지우고 개수를 로그에(0건이면 로그 없음)
deleted = []
repository.delete_archive_history_older_than = lambda days: (deleted.append(days), 5)[1]
for raw in (None, "", "0", "-1"): reset(); deleted.clear(); repository.set_setting("archive_history_retention_days", raw); run(); assert deleted == [], raw
reset(); deleted.clear(); repository.set_setting("archive_history_retention_days", "60"); run(); assert deleted == [60] and "보관기간(60일) 초과 아카이빙 이력 5건 정리" in text()
repository.delete_archive_history_older_than = lambda days: 0
reset(); repository.set_setting("archive_history_retention_days", "60"); run(); assert "초과 아카이빙 이력" not in text()
print("ALL OK")
