"""이력 보관 기간 정리(app/retention.py) — 실행 이력/다운로드 이력/아카이빙 이력 세 곳이 같은 규칙을 쓴다(예전에는 같은 함수가 세 군데에 복사돼 있었다).
설정(일수)이 없거나 0 이하이거나 숫자가 아니면 아무것도 지우지 않고, 양수면 그 일수보다 오래된 이력을 지우고 지운 개수가 있으면 해당 작업 로그에 남긴다.
"""
from app import db, job_status, repository, retention
db.get_connection()

deleted_calls = []
def deleter(count):
    def delete(days): deleted_calls.append(days); return count
    return delete
log_of = lambda name: "\n".join(job_status.snapshot()[name]["log"])

# Scenario 1: 설정 읽기 — 양수만 일수로, 나머지(없음/빈 값/0/음수/숫자 아님)는 None(정리 안 함)
for raw, expected in ((None, None), ("", None), ("0", None), ("-5", None), ("abc", None), ("3.5", None), ("30", 30), (" 45 ", 45)):
    repository.set_setting("test_retention_days", raw); assert retention.retention_days("test_retention_days") == expected, (raw, retention.retention_days("test_retention_days"))
print("1) 설정 읽기 OK")

# Scenario 2: 정리 — 설정이 없으면 지우는 함수를 부르지 않고 0, 있으면 그 일수로 지우고 개수를 돌려준다. 지운 게 있을 때만 해당 작업 로그에 한 줄
job_status.start("download"); repository.set_setting("test_retention_days", None)
assert retention.cleanup_history("download", "test_retention_days", deleter(9), "실행 이력") == 0 and deleted_calls == []
repository.set_setting("test_retention_days", "30")
assert retention.cleanup_history("download", "test_retention_days", deleter(4), "실행 이력") == 4 and deleted_calls == [30]
assert "보관기간(30일) 초과 실행 이력 4건 정리" in log_of("download")
job_status.start("download"); assert retention.cleanup_history("download", "test_retention_days", deleter(0), "다운로드 이력") == 0 and "정리" not in log_of("download"), "지운 게 없으면 로그 없음"
job_status.start("archive"); retention.cleanup_history("archive", "test_retention_days", deleter(2), "아카이빙 이력"); assert "보관기간(30일) 초과 아카이빙 이력 2건 정리" in log_of("archive"), "로그는 지정한 작업에"
print("2) 정리 OK")

# Scenario 3: 세 정리 함수가 각자의 설정 키/지우는 함수/문구로 이 규칙을 쓴다
import app.scheduler.download_job as dj
import app.scheduler.archive_job as aj
seen = []
for attr in ("delete_job_history_older_than", "delete_episode_history_older_than", "delete_archive_history_older_than"):
    setattr(repository, attr, lambda days, attr=attr: (seen.append((attr, days)), 1)[1])
for key in ("job_history_retention_days", "episode_history_retention_days", "archive_history_retention_days"): repository.set_setting(key, "7")
for name in ("download", "archive"): job_status.start(name)
dj._cleanup_old_job_history(); dj._cleanup_old_episode_history(); aj._cleanup_old_archive_history()
assert seen == [("delete_job_history_older_than", 7), ("delete_episode_history_older_than", 7), ("delete_archive_history_older_than", 7)], seen
assert "초과 실행 이력 1건" in log_of("download") and "초과 다운로드 이력 1건" in log_of("download") and "초과 아카이빙 이력 1건" in log_of("archive")
print("ALL OK")
