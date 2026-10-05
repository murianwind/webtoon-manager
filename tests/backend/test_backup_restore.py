"""백업/복원 — 카카오 데이터까지 빠짐없이, 같은 컨테이너와 완전히 새로운 컨테이너 양쪽에서.
같은 컨테이너: 지금 쓰는 비밀값(웹훅/봇 토큰/카카오 쿠키)을 지우지 않는다. 새 컨테이너: 환경이 다른 경로/폴더/rclone 설정/다시 입력할 항목을 알려 준다."""
import asyncio, json, tempfile, time
from pathlib import Path

import httpx

from app import (backup_restore, db, discord_config, download_roots, kakao_catalog, kakao_page_auth as auth, kakao_page_download as kp,
                 repository, schedule_config, scheduler as scheduler_mod)
from app.config import get_settings
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
db.get_connection()
import app.main as m

ST = get_settings()
TABLES = ("webtoons", "settings", "watched_authors", "watched_tags", "kakao_seen_titles", "kakao_webtoons", "filename_template_presets",
          "archive_targets", "archive_history", "episode_history", "archive_pending_finish")
# 비밀값은 백업 글자 어디에도 없어야 하므로, 시각/숫자와 우연히 겹칠 수 없는 고유한 값을 쓴다(짧은 숫자는 가끔 겹쳐서 실패했다)
SECRET = {"discord_webhook_url": "https://discord.com/api/webhooks/1/SECRET-HOOK", "discord_bot_token": "BOT-TOKEN-ABC", "discord_notify_channel_id": "CHANNEL-ID-SECRET-7731"}

def wipe_everything():                                   # 완전히 새 컨테이너(빈 데이터 볼륨)처럼
    with db.write_transaction() as cx:
        for t in (*TABLES, "job_history"): cx.execute(f"DELETE FROM {t}")
def populate():
    repository.upsert_new(title_id="111", title="네이버작", added_source=repository.SOURCE_MANUAL); repository.update_last_downloaded_no("111", 7)
    repository.upsert_new(title_id="222", title="성인작", added_source=repository.SOURCE_MANUAL); repository.update_is_adult("222", True)
    for sid, title, status in ((101, "카카오 구독작", "active"), (102, "카카오 해제작", "unsubscribed"), (103, "카카오: 제외작", "excluded")):
        repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE, author_summary="글쓴이, 원작가")
        with db.write_transaction() as cx: cx.execute("UPDATE kakao_webtoons SET status = ? WHERE title_id = ?", (status, sid))
    repository.set_kakao_authors(101, ["글쓴이"], ["원작가"]); repository.set_kakao_finished(101, True); repository.set_kakao_finish_notified(101)
    repository.upsert_watched_author("원작가", "원작가", True, "kakao"); repository.upsert_watched_author("꺼둔작가", "꺼둔작가", False, "kakao")
    repository.upsert_watched_author("5001", "네이버작가", True, "naver")
    repository.upsert_watched_tag("T1", "로맨스", True) if hasattr(repository, "upsert_watched_tag") else None
    with db.write_transaction() as cx: cx.execute("INSERT OR REPLACE INTO kakao_seen_titles (author_name, title_id, title_name, seen_at) VALUES ('원작가', 101, '카카오 구독작', 'x')")
    repository.add_episode_history("101", "카카오 구독작", 3, "3화", "success", platform="kakao"); repository.add_episode_history("111", "네이버작", 7, "7화", "success")
    repository.upsert_archive_target("kakao_101", "보관/카카오", True, "local")
    repository.upsert_archive_target("111", "r:웹툰/네이버", True, "rclone")
    repository.add_archive_history("kakao_101", "카카오 구독작", "0001_1화.zip", "periodic"); repository.add_pending_finish_archive("kakao_101")
    schedule_config.set_download_schedules([schedule_config.JobSchedule(mode="interval", interval_minutes=30, target="kakao"), schedule_config.JobSchedule(mode="interval", interval_minutes=240, target="naver")])
    for k, v in (("kakao_webtoons_enabled", "1"), ("report_last_sent_at", "2026-10-01T00:00:00+00:00"), ("report_seen_new_episodes", '{"dl:kakao:101:3":"1"}'), ("kakao_legacy_migrated", "1"),
                 ("app_public_base_url", "https://webtoon.example.com"), ("webtoon_server_url", "https://komga.example.com"), ("archive_conflict_policy", "rename")): repository.set_setting(k, v)
def set_secrets(with_kakao_cookie=True):
    discord_config.set_webhook_url(SECRET["discord_webhook_url"]); discord_config.set_bot_token(SECRET["discord_bot_token"]); discord_config.set_notify_channel_id(SECRET["discord_notify_channel_id"])
    if with_kakao_cookie:    # 실제 저장 함수로 유효한 로그인 쿠키를 넣는다(복호화돼서 읽혀야 "이미 있음"으로 판단)
        auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": f"value-{n}-0123456789", "path": "/", "expirationDate": time.time() + 9e5} for n in auth.REQUIRED_COOKIES])))
def snapshot():
    data = repository.export_all(); data.pop("_meta", None); return json.loads(json.dumps(data, sort_keys=True))

# ═══ 1. 백업 내용: 카카오 데이터 포함, 비밀값/상태값은 제외, 형식 버전 ═══
populate(); set_secrets()
repository.set_setting("kakao_page_alert_expired_at", "2026-10-01T00:00:00"); repository.set_setting("adult_cookie_expired_notified", "1"); repository.set_setting("kakao_catalog_snapshot", "{큰캐시}")
backup = repository.export_all()
assert backup["_meta"]["format_version"] == repository.BACKUP_FORMAT_VERSION and backup["_meta"]["created_at"]
text = json.dumps(backup, ensure_ascii=False)
for secret in (*SECRET.values(), f"value-{auth.REQUIRED_COOKIES[0]}-0123456789", "큰캐시"): assert secret not in text, f"백업에 들어가면 안 되는 값: {secret}"
keys = {r["key"] for r in backup["settings"]}
assert not keys & {"discord_webhook_url", "discord_bot_token", "discord_notify_channel_id", "kakao_page_cookies", "kakao_catalog_snapshot", "kakao_page_alert_expired_at", "adult_cookie_expired_notified"}, keys
for needed in ("kakao_webtoons_enabled", "report_seen_new_episodes", "kakao_legacy_migrated", "app_public_base_url", "webtoon_server_url", "archive_conflict_policy"): assert needed in keys, needed
kw = {r["title_id"]: r for r in backup["kakao_webtoons"]}
assert json.loads(kw[101]["writer_names"]) == ["글쓴이"] and json.loads(kw[101]["origin_names"]) == ["원작가"] and kw[101]["is_finished"] == 1 and kw[101]["finish_notified"] == 1   # 카카오 작가/완결 상태
assert any(r["platform"] == "kakao" for r in backup["episode_history"]) and any(r["title_id"] == "kakao_101" for r in backup["archive_targets"]) and backup["archive_pending_finish"]
print("1) 백업 내용 OK (카카오 작가/완결/이력/아카이빙 대상 포함, 비밀값·상태값 제외, 형식 버전)")

# ═══ 2. 완전히 새로운 컨테이너: 모든 데이터를 비운 뒤 복원 → 같은 내용 ═══
before = snapshot()
wipe_everything()
for k in ("discord_webhook_url", "discord_bot_token", "discord_notify_channel_id", "kakao_page_cookies", "kakao_catalog_snapshot", "kakao_page_alert_expired_at", "adult_cookie_expired_notified"): repository.set_setting(k, None)
KROOT = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(KROOT))
report = backup_restore.restore_backup(json.loads(json.dumps(backup)), ST)
after = snapshot()
# 환경에 따라 달라져서 검증 대상이 아닌 것: 방금 테스트가 임시로 정한 카카오 폴더 설정(복원이 backup의 경로로 덮어씀)
for side in (before, after): side["settings"] = [r for r in side["settings"] if r["key"] != "kakao_download_root"]
assert before == after, {k: (len(before[k]), len(after[k])) for k in before if before[k] != after[k]}
print("2) 새 컨테이너 복원 OK (11개 테이블 전부 같은 내용)")

# ═══ 3. 새 컨테이너: 다시 입력할 항목과 환경 점검 안내 ═══
assert report["status"] == "restored"
reenter = " | ".join(report["reenter"])
assert "디스코드 웹훅" in reenter and "카카오페이지 로그인 쿠키" in reenter and "성인 인증" in reenter and "봇 토큰" in reenter, reenter     # 비밀값은 백업에 없으니 다시 입력
warnings = " | ".join(report["warnings"])
assert "카카오페이지 구독 작품 1개의 폴더가 없습니다" in warnings and "자동 다운로드가 그 작품을 처음부터 전부 받습니다" in warnings, warnings    # 폴더가 안 붙은 새 컨테이너의 위험
assert "rclone" in warnings                                                                                          # rclone 원격을 쓰는 아카이빙 대상이 있는데 설정 파일이 없음
print("3) 다시 입력할 항목/환경 경고 OK")

# ═══ 4. 환경이 다른 다운로드 폴더 경로: 이 환경에 없으면 기본 폴더로 되돌리고 알린다 / 있으면 그대로 ═══
data = json.loads(json.dumps(backup)); good = tempfile.mkdtemp()
data["settings"] += [{"key": download_roots.NAVER_ROOT_SETTING_KEY, "value": "/다른환경/Webtoon_Download"}, {"key": kp.DOWNLOAD_ROOT_SETTING_KEY, "value": good}]
rep = backup_restore.restore_backup(data, ST)
assert repository.get_setting(download_roots.NAVER_ROOT_SETTING_KEY) is None and repository.get_setting(kp.DOWNLOAD_ROOT_SETTING_KEY) == good
assert any("네이버" in w and "/다른환경/Webtoon_Download" in w and "기본 폴더" in w for w in rep["warnings"]) and not any("카카오페이지 다운로드 폴더(" in w for w in rep["warnings"]), rep["warnings"]
print("4) 다운로드 폴더 경로 점검 OK")

# ═══ 5. 폴더가 실제로 연결돼 있으면 폴더 경고가 없다(작품 폴더 이름은 플랫폼별 규칙) ═══
(KROOT / remove_forbidden_str_kakao("카카오 구독작")).mkdir(); naver_root = Path(download_roots.naver_root(ST)); (naver_root / remove_forbidden_str("네이버작")).mkdir(parents=True, exist_ok=True)
(naver_root / remove_forbidden_str("성인작")).mkdir(exist_ok=True); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(KROOT))
issues = backup_restore.check_environment(ST)
assert not [w for w in issues["warnings"] if "폴더가 없습니다" in w], issues["warnings"]
print("5) 폴더 연결 시 경고 없음 OK")

# ═══ 6. 같은 컨테이너에서 복원: 지금 쓰는 비밀값을 지우지 않는다 + 백업 파일 속 비밀값은 무시 ═══
set_secrets(); repository.set_setting("kakao_page_alert_soon_at", "2026-10-01T00:00:00"); repository.set_setting("adult_cookie_expired_notified", "1"); repository.set_setting("kakao_catalog_snapshot", "{캐시}")
tampered = json.loads(json.dumps(backup)); tampered["settings"] += [{"key": "discord_webhook_url", "value": "https://evil.example/hook"}, {"key": "kakao_page_cookies", "value": "가짜"}]
rep = backup_restore.restore_backup(tampered, ST)
assert discord_config.get_webhook_url() == SECRET["discord_webhook_url"] and discord_config.get_bot_token() == SECRET["discord_bot_token"] and discord_config.get_notify_channel_id() == SECRET["discord_notify_channel_id"]
assert auth.load_cookies() and repository.get_setting("kakao_catalog_snapshot") == "{캐시}"      # 카카오 쿠키/캐시 유지, 조작된 값("가짜")은 무시
assert repository.get_setting("kakao_page_cookies") != "가짜"
assert repository.get_setting("kakao_page_alert_soon_at") is None and repository.get_setting("adult_cookie_expired_notified") is None           # 알림 기록은 초기화 → 새 환경에서 다시 알릴 수 있다
assert "디스코드 웹훅" not in " | ".join(rep["reenter"]) and "카카오페이지 로그인 쿠키" not in " | ".join(rep["reenter"]), rep["reenter"]               # 이미 있으니 다시 입력 안내 없음
print("6) 같은 컨테이너 복원 OK (비밀값 유지, 조작된 값 무시, 알림 기록 초기화)")

# ═══ 7. 안전장치: 백업 파일이 아니거나 더 새로운 버전이면 거부하고 데이터는 그대로 ═══
snap = snapshot()
for bad, why in (({}, "빈 JSON"), ({"foo": 1}, "엉뚱한 JSON"), ([], "객체 아님"), ({"webtoons": [], "_meta": {"format_version": repository.BACKUP_FORMAT_VERSION + 1}}, "더 새로운 버전")):
    try: backup_restore.restore_backup(bad, ST); raise AssertionError(f"거부돼야 함: {why}")
    except ValueError as e: assert str(e), why
    assert snapshot() == snap, f"거부했는데 데이터가 바뀜: {why}"
print("7) 잘못된 백업 거부 OK (데이터 그대로)")

# ═══ 8. 예전 형식 백업(_meta 없음, 나중에 생긴 컬럼 없음)도 복원된다 ═══
old = {"webtoons": [{"title_id": "9", "title": "옛웹툰", "status": "active", "created_at": "x", "updated_at": "x"}],
       "kakao_webtoons": [{"title_id": 55, "title": "옛카카오", "status": "active", "ever_subscribed": 1, "thumbnail_url": "", "author_summary": "", "created_at": "x", "updated_at": "x"}],
       "episode_history": [{"id": 1, "title_id": "9", "title_name": "옛웹툰", "episode_no": 1, "subtitle": "", "status": "success", "error_msg": "", "downloaded_at": "x"}],
       "settings": [{"key": "app_public_base_url", "value": "https://old"}]}
backup_restore.restore_backup(old, ST)
w = repository.get_kakao_webtoon(55); assert w["status"] == "active" and w["writer_names"] == [] and w["is_finished"] is False
assert repository.list_episode_history_since("0")[0]["platform"] == "naver" and repository.get_setting("app_public_base_url") == "https://old"
print("8) 예전 형식 백업 OK")

# ═══ 9. 실행 이력(job_history)은 백업/복원 대상이 아니라 그대로 남는다 ═══
populate()
with db.write_transaction() as cx: cx.execute("INSERT INTO job_history (job_name, started_at, finished_at, status, log) VALUES ('download', 'x', 'x', 'success', '[]')")
jh_before = repository.fetchall("SELECT COUNT(*) AS c FROM job_history")[0]["c"]; assert jh_before >= 1
backup_restore.restore_backup(json.loads(json.dumps(repository.export_all())), ST); assert repository.fetchall("SELECT COUNT(*) AS c FROM job_history")[0]["c"] == jh_before
print("9) 실행 이력 유지 OK")

# ═══ 10. API: 복원하면 스케줄을 다시 적용하고, 카카오가 켜져 있으면 목록을 새로 채운다 + 결과(경고/다시 입력)를 돌려준다 ═══
calls = {"resched": 0, "refresh": 0}
scheduler_mod.reschedule_all = lambda s: calls.__setitem__("resched", calls["resched"] + 1)
kakao_catalog.start_refresh = lambda: calls.__setitem__("refresh", calls["refresh"] + 1) or True
class FakeScheduler: pass
m.app.state.scheduler = FakeScheduler()
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        populate(); body = (await c.get("/api/backup")).json(); assert body["_meta"]["format_version"] == repository.BACKUP_FORMAT_VERSION
        r = await c.post("/api/restore", json=body); j = r.json()
        assert r.status_code == 200 and j["status"] == "restored" and isinstance(j["warnings"], list) and isinstance(j["reenter"], list), r.text
        assert calls == {"resched": 1, "refresh": 1}, calls                                                           # 복원 즉시 스케줄 반영 + 카카오 목록 갱신
        body["settings"] = [s for s in body["settings"] if s["key"] != "kakao_webtoons_enabled"] + [{"key": "kakao_webtoons_enabled", "value": "0"}]
        await c.post("/api/restore", json=body); assert calls == {"resched": 2, "refresh": 1}, calls                   # 카카오가 꺼져 있으면 목록을 갱신하지 않는다
        snap = snapshot()
        for bad in ({}, {"x": 1}, [1, 2]):
            r = await c.post("/api/restore", json=bad); assert r.status_code in (400, 422), (bad, r.status_code)
        assert snapshot() == snap and calls["resched"] == 2                                                           # 거부된 복원은 아무것도 안 바꾸고 스케줄도 안 건드림
asyncio.run(api())
print("10) /api/restore OK (스케줄 재적용, 카카오 목록 갱신, 잘못된 입력 거부)")
print("\n전부 통과")
