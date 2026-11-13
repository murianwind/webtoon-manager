"""아카이빙 작업(scheduler/archive_job.py) — 정기 아카이빙 + 완결 대기열을 돌리고, 문제(이름 충돌/이동 실패)를 모아서 한 번에 알리며(실패는 한 줄 사유로 줄여 같은 사유끼리 묶고, 오류 전문은 알림에 싣지 않는다 — 로그에 남는다), 뷰어 서버가 있으면 "읽은 회차만 옮긴다".
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
assert len(sent) == 1 and "📦 **아카이빙 실행 알림**" in sent[0] and "⚠️ 파일명 충돌 1건" in sent[0] and "- 충돌작품: 001.zip (이름 바꿔 저장)" in sent[0] and "❌ 이동 실패 1건" in sent[0] and "- 권한 없음 (1건): 실패작품: 002.zip" in sent[0], sent
assert "🔎 자세한 오류는" in sent[0] and "로그" in sent[0], sent
print("5) 문제 모아서 알림 OK")
msg = []
async def collect(session, text): msg.append(text)
discord_notify.send_webhook_notification = collect
asyncio.run(aj._notify_archive_issues(None, [("a", "1.zip", "skip"), ("b", "2.zip", "overwrite"), ("c", "3.zip", "새정책")], []))
assert "(건너뜀)" in msg[0] and "(덮어씀)" in msg[0] and "(새정책)" in msg[0] and "이동 실패" not in msg[0], msg                      # 정책 이름은 한글로, 모르는 정책은 그대로
discord_notify.send_webhook_notification = discord_fake(sent)

# Scenario 5b: 알림은 사유를 한 줄로 줄이고 같은 사유끼리 묶는다 — rclone 오류 전문(여러 줄)을 파일마다 싣지 않는다. 자세한 오류는 로그에 있다고 안내한다
RCLONE_401 = ("2026/11/13 08:18:34 NOTICE: 0015_15화 (3).zip: Failed to cancel multipart upload: HTTP error 401 (401 Unauthorized) returned body: \"\"\n"
              "2026/11/13 08:18:34 ERROR : 0015_15화.zip: Failed to copy: HTTP error 401 (401 Unauthorized) returned body: \"\"\n2026/11/13 08:18:34 ERROR : Attempt 1/3 failed with 1 errors and: HTTP error 401")
RCLONE_503 = 'low level retry 1/10 (error HTTP error 503 (503 Service Unavailable) returned body: "<!DOCTYPE html>...Sharepoint Online...")'
build = lambda c, f: aj.build_archive_issue_message(c, f)
five = [(f"작품{i}", f"00{i}_{i}화.zip", RCLONE_401) for i in range(1, 6)]
m1 = build([], five)
assert m1.count("❌ 이동 실패 5건") == 1 and "- 보관 위치 접근 거절(401) — 서비스 장애나 로그인 만료 가능 (5건): 작품1: 001_1화.zip, 작품2: 002_2화.zip, 작품3: 003_3화.zip 외 2건" in m1, m1
assert "Attempt" not in m1 and "returned body" not in m1 and "2026/11/13" not in m1 and "Failed to copy" not in m1, m1                  # 오류 전문은 알림에 없다
assert "🔎 자세한 오류는 앱의 아카이빙 로그(수동 실행 화면 · 설정의 실행 이력)에서 확인하세요." in m1 and len(m1) < 400, (len(m1), m1)
mixed = [("A", "1.zip", RCLONE_401), ("B", "2.zip", RCLONE_503), ("C", "3.zip", RCLONE_401), ("D", "4.zip", "특이한 오류"), ("E", "5.zip", RCLONE_503), ("F", "6.zip", RCLONE_401)]
m2 = build([], mixed).split("\n")
bullets = [l for l in m2 if l.startswith("- ")]
assert bullets == ["- 보관 위치 접근 거절(401) — 서비스 장애나 로그인 만료 가능 (3건): A: 1.zip, C: 3.zip, F: 6.zip",
                   "- 보관 위치 서버 일시 오류(5xx) — 잠시 후 다시 시도 (2건): B: 2.zip, E: 5.zip", "- 특이한 오류 (1건): D: 4.zip"], bullets          # 사유별로 묶고, 처음 나온 순서
assert "❌ 이동 실패 6건" in m2
# 사유가 많으면 앞의 5가지만, 나머지는 한 줄로. 충돌도 5건까지만
many_reasons = [(f"작품{i}", f"{i}.zip", f"서로 다른 오류 {i}") for i in range(8)]
m3 = build([("c%d" % i, f"{i}.zip", "rename") for i in range(8)], many_reasons)
assert sum(1 for l in m3.split("\n") if l.startswith("- ") and "건):" in l) == 5 and "- 그 밖의 사유 3가지 (3건)" in m3, m3
assert "⚠️ 파일명 충돌 8건" in m3 and "- 외 3건" in m3 and m3.count("(이름 바꿔 저장)") == 5, m3
# 아주 많은 실패와 아주 긴 오류여도 디스코드 글자 제한(2000자) 안에 들어간다 — 요약만 나가므로 오류 길이와 무관
huge = build([("c", f"{i}.zip", "skip") for i in range(300)], [(f"긴제목{i}" * 5, f"{i}" * 40 + ".zip", ("x" * 3000) + str(i)) for i in range(200)])
assert len(huge) <= 1900 and "❌ 이동 실패 200건" in huge and huge.rstrip().endswith("확인하세요."), (len(huge), huge[-200:])
# 제목/파일 이름이 아주 길어서 사유가 가짓수 제한 안인데도 길이를 넘는 경우 — 안내 줄은 남기고 중간을 줄여 글자 제한을 지킨다
long_names = build([], [("T" * 400 + str(i), "f" * 300 + ".zip", RCLONE_401) for i in range(3)] + [("U" * 400, "g" * 300 + ".zip", RCLONE_503)])
assert len(long_names) <= 1900 and "…" in long_names and long_names.endswith("확인하세요.") and "❌ 이동 실패 4건" in long_names, (len(long_names), long_names[-150:])
# 충돌만 있으면 실패 부분과 로그 안내는 없다. 실패만 있으면 충돌 부분이 없다
only_conflicts = build([("a", "1.zip", "skip")], []); assert "⚠️ 파일명 충돌 1건" in only_conflicts and "이동 실패" not in only_conflicts and "🔎" not in only_conflicts, only_conflicts
only_failures = build([], five[:1]); assert "충돌" not in only_failures and "❌ 이동 실패 1건" in only_failures
print("5b) 알림은 한 줄 사유로 묶어서 OK")

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
