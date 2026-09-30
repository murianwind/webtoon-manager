"""카카오 완결 감지/알림(디스코드 버튼) + 리포트의 카카오 표시 + 기존 DB 마이그레이션 + 백업."""
import asyncio, json, os, sqlite3, subprocess, sys, tempfile, time
from http.cookies import SimpleCookie
from pathlib import Path
import aiohttp

from app import db, repository, kakao_page_auth as auth, kakao_page_download as kp, job_status, scheduler, discord_bot
from app.config import get_settings
db.get_connection()

# ── 가짜 카카오 서버 ──
S = {"series": {}}
class R:
    def __init__(self, status=200, data=None, body=b"", ctype="application/json"):
        self.status, self._d, self._b = status, data, body; self.headers = {"Content-Type": ctype}; self.cookies = SimpleCookie()
    async def json(self, content_type=None): return self._d
    async def read(self): return self._b
    async def text(self, errors="strict"): return ""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
def eps(spec, title):
    return [{"cursor_index": o, "item": {"product_id": o, "title": f"{title} {sub}", "is_free": free, "order_value": o, "page_count": 2, "hidden": False, "slide_type": "SD03",
             "service_property": {"purchase_info": {"purchase_type": "not_purchased"}}}} for o, sub, free in spec]
def handler(method, url, params, data):
    if url.endswith("/user/get_profile"): return R(data={"result_code": 0, "profile": {"uid": 1}})
    if "product/list" in url:
        info = S["series"][params["series_id"]]
        return R(data={"result": {"series_item": {"title": info["title"], "on_issue": info["on_issue"]}, "list": eps(info["spec"], info["title"]), "has_next": False}})
    if url.endswith("/ticket/my"): return R(data={"result_code": 0, "result": {"waitfree": {"charged_complete": False, "charged_at": "2099-01-01T00:00:00+09:00"}, "my": {}}})
    if "content/about" in url: return R(data={"result_code": 0, "result": {"author_list": []}})
    if "viewer/data" in url:
        return R(data={"viewer_data": {"imageDownloadData": {"files": [{"no": i, "secureUrl": f"https://dw-img-page.kakao.com/sdownload/resource?token=t{params['product_id']}_{i}"} for i in (1, 2)]}}})
    if "sdownload" in url: return R(body=b"\xff\xd8\xffx", ctype="image/jpeg")
    raise AssertionError(url)
class FakeSession:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, params=None, headers=None, timeout=None): return handler("GET", str(url), dict(params or {}), None)
    def post(self, url, data=None, headers=None, timeout=None): return handler("POST", str(url), {}, data)
aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
kp.asyncio.sleep = _ns; scheduler.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"
FREE = lambda n: [(i, f"{i}화", True) for i in range(1, n + 1)]
sent = []; BOT = {"ready": True}
async def fake_prompt(title_id, title, platform="naver"):
    if not BOT["ready"]: return False
    sent.append((title_id, title, platform)); return True
scheduler.discord_bot.send_completion_prompt = fake_prompt

async def main():
    settings = get_settings(); root = Path(tempfile.mkdtemp())
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(root))
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])))
    job_status.start("download")
    fin = lambda sid: repository.get_kakao_webtoon(sid)

    # ── 1. RunResult.nothing_left ──
    E = lambda n, acc=True: kp.Episode(product_id=n, title="t", number=n, subtitle=f"{n}화", is_free=acc, accessible=acc, page_count=1, hidden=False)
    plan = lambda todo, locked: kp.DownloadPlan(mode="compare", to_download=[E(n) for n in todo], locked=[E(n, False) for n in locked])
    assert kp.RunResult([1, 2], [], None, plan([1, 2], []), finished=True).nothing_left is True                      # 다 받음
    assert kp.RunResult([1], [], None, plan([1, 2], []), finished=True).nothing_left is False                        # 상한/중단으로 덜 받음
    assert kp.RunResult([1, 2], [], None, plan([1], [2]), ticket_used=2).nothing_left is True                        # 마지막 잠긴 회차를 기다무로 열어 받음
    assert kp.RunResult([1, 2], [], None, plan([1], [2, 3]), ticket_used=2).nothing_left is False                    # 잠긴 회차가 더 남음
    assert kp.RunResult([1], [], None, plan([1], [2])).nothing_left is False                                         # 잠긴 회차 대기
    assert kp.RunResult([], [], 3, plan([3], [])).nothing_left is False and kp.RunResult([], [], None, plan([], []), "오류").nothing_left is False
    print("1) 남은 회차 판정 OK")

    # ── 2. 스케줄러: 완결 감지 + 알림 ──
    for sid, title in ((10, "완결작"), (20, "잠긴회차있는완결작"), (30, "연재작")):
        repository.upsert_new_kakao_webtoon(sid, title, status=repository.STATUS_ACTIVE)
    S["series"] = {10: {"title": "완결작", "on_issue": "N", "spec": FREE(3)}, 20: {"title": "잠긴회차있는완결작", "on_issue": "N", "spec": FREE(2) + [(3, "3화", False)]},
                   30: {"title": "연재작", "on_issue": "Y", "spec": FREE(2)}}
    failures = []; await scheduler._download_kakao_subscriptions(settings, failures)
    assert fin(10)["is_finished"] and fin(10)["finish_notified"] and not fin(20)["is_finished"] and not fin(30)["is_finished"], [fin(i) for i in (10, 20, 30)]
    assert sent == [("10", "완결작", "kakao")]                                                                        # 완결이고 다 받은 작품만, 카카오로 표시해서 알림
    assert {r["platform"] for r in repository.list_episode_history_since("2000-01-01")} == {"kakao"}                  # 이력에 플랫폼
    await scheduler._download_kakao_subscriptions(settings, failures); assert len(sent) == 1                          # 다시 돌려도 중복 알림 없음
    S["series"][30]["on_issue"] = "N"; await scheduler._download_kakao_subscriptions(settings, failures)              # 연재작이 나중에 완결
    assert fin(30)["is_finished"] and sent[-1] == ("30", "연재작", "kakao") and len(sent) == 2
    repository.acknowledge_kakao_finish(30); S["series"][30]["on_issue"] = "Y"; await scheduler._download_kakao_subscriptions(settings, failures)
    assert not fin(30)["is_finished"] and not fin(30)["finish_ack"] and not fin(30)["finish_notified"]                # 다시 연재되면 기록 초기화(다음 완결 때 다시 알림)
    print("2) 완결 감지 OK (완결+다 받음일 때만, 중복 없음, 연재 재개 시 초기화)")

    # ── 3. 봇이 꺼져 있으면 알렸다고 기록하지 않고 다음에 다시 시도 ──
    repository.upsert_new_kakao_webtoon(40, "봇꺼짐작", status=repository.STATUS_ACTIVE); S["series"][40] = {"title": "봇꺼짐작", "on_issue": "N", "spec": FREE(1)}
    BOT["ready"] = False; before = len(sent)
    await scheduler._download_kakao_subscriptions(settings, failures)
    assert fin(40)["is_finished"] and not fin(40)["finish_notified"] and len(sent) == before
    BOT["ready"] = True; await scheduler._notify_newly_finished()                                                   # 완결 알림 재시도(디스커버리 잡 경로)
    assert fin(40)["finish_notified"] and sent[-1] == ("40", "봇꺼짐작", "kakao")
    repository.set_kakao_webtoon_status(40, repository.STATUS_UNSUBSCRIBED); assert repository.list_kakao_finish_pending() == []   # 구독 중이 아니면 대상 아님
    print("3) 봇 꺼짐 재시도 OK")

    # ── 4. 디스코드 버튼 ──
    class Resp:
        def __init__(self): self.msgs, self.edits = [], []; self._done = False
        async def send_message(self, content, ephemeral=False): self.msgs.append((content, ephemeral)); self._done = True
        async def edit_message(self, content=None, view=None): self.edits.append(content); self._done = True
        def is_done(self): return self._done
    class Inter:
        def __init__(self, custom_id):
            import discord; self.type = discord.InteractionType.component; self.data = {"custom_id": custom_id}; self.response = Resp()
    bot = discord_bot.CompletionBotClient(1)
    repository.upsert_new_kakao_webtoon(50, "버튼작", status=repository.STATUS_ACTIVE); repository.set_kakao_finished(50, True)
    i = Inter("kakao_ack:50"); await bot.on_interaction(i)
    assert fin(50)["finish_ack"] and fin(50)["status"] == "active" and "[카카오] 버튼작" in i.response.edits[0] and "구독은 유지" in i.response.edits[0]      # 알람 제외
    repository.set_setting("archive_on_finish_unsubscribe", "1")
    i = Inter("kakao_unsubscribe:50"); await bot.on_interaction(i)
    assert fin(50)["status"] == "unsubscribed" and "구독해제" in i.response.edits[0]                                                              # 구독해제
    assert "kakao_50" in repository.list_pending_finish_archive()                                                                                  # 완결 알림 버튼으로 구독해제해도 자동 이동 대기열
    repository.upsert_new(title_id="888", title="네이버완결", added_source=repository.SOURCE_MANUAL); repository.mark_finished("888")
    i = Inter("webtoon_unsubscribe:888"); await bot.on_interaction(i); assert "888" in repository.list_pending_finish_archive()                    # 네이버 버튼 경로도 같다
    repository.set_setting("archive_on_finish_unsubscribe", None)
    i = Inter("kakao_unsubscribe:987654"); await bot.on_interaction(i); assert i.response.msgs[0][0] == "이미 처리된 웹툰입니다." and i.response.msgs[0][1] is True
    i = Inter("kakao_unsubscribe:abc"); await bot.on_interaction(i); assert "오류" in i.response.msgs[0][0]                                        # 잘못된 값도 봇이 죽지 않는다
    class Chan:
        def __init__(self): self.sent = []
        async def send(self, content, view): self.sent.append((content, [(b.label, b.custom_id) for b in view.children]))
    chan = Chan(); bot.get_channel = lambda cid: chan
    await bot.send_completion_prompt("50", "버튼작", "kakao"); await bot.send_completion_prompt("777", "네이버작")
    assert chan.sent[0][1] == [("구독해제", "kakao_unsubscribe:50"), ("알람 제외", "kakao_ack:50")] and "[카카오]" in chan.sent[0][0]
    assert chan.sent[1][1] == [("구독해제", "webtoon_unsubscribe:777"), ("알람 제외", "webtoon_ack:777")] and "[카카오]" not in chan.sent[1][0]     # 네이버는 예전 그대로
    print("4) 디스코드 버튼 OK (카카오 알람 제외/구독해제, 잘못된 값, 네이버 버튼은 그대로)")

    # ── 5. 리포트 ──
    row = lambda platform, title, no, status="success", tid="1": {"platform": platform, "title_name": title, "episode_no": no, "title_id": tid, "status": status, "subtitle": "", "error_msg": ""}
    naver_only = scheduler._build_report_message([row("naver", "네이버작", 1, tid="100")], [], {"네이버작": "http://v/n"})
    assert "• 네이버작 [바로가기](<http://v/n>)" in naver_only and "[네이버]" not in naver_only                          # 네이버만 있으면 예전 형식 그대로
    mixed = scheduler._build_report_message(
        [row("naver", "네이버작", 1, tid="100"), row("kakao", "쌍갑포차", 409, tid="61641075"), row("kakao", "쌍갑포차", 411, tid="61641075"), row("kakao", "쌍갑포차", 410, tid="61641075"), row("kakao", "개미", 5, tid="68239972")],
        [row("kakao", "실패작", 3, "failed", "5"), row("naver", "네이버실패", 2, "failed", "6")], {"kakao:개미": "http://v/ant"})
    assert "📁 받은 작품 (3):" in mixed
    assert "• [네이버] 네이버작" in mixed and "• [카카오] 개미 · 1개 회차(5번) [바로가기](<http://v/ant>)" in mixed                                    # 뷰어 링크가 있으면 뷰어로
    assert "• [카카오] 쌍갑포차 · 3개 회차(409~411번) [바로가기](<https://page.kakao.com/content/61641075>)" in mixed                                # 없으면 카카오페이지 링크, 회차 수/범위
    assert mixed.index("[네이버] 네이버작") < mixed.index("[카카오] 개미")                                                                          # 네이버가 먼저
    assert "❌ 실패한 작품 (2):" in mixed and "[네이버] 네이버실패" in mixed and "[카카오] 실패작" in mixed
    same_name = scheduler._build_report_message([row("naver", "같은제목", 1), row("kakao", "같은제목", 2, tid="9")], [], {})
    assert "받은 작품 (2)" in same_name                                                                                                            # 이름이 같아도 플랫폼이 다르면 따로 센다
    print("5) 리포트 OK (플랫폼 표시, 카카오 회차 수/범위, 링크 대체, 네이버 형식 유지)")

    # ── 6. 백업/복원 ──
    repository.set_kakao_finished(10, True); b = repository.export_all()
    assert [x for x in b["kakao_webtoons"] if x["title_id"] == 10][0]["is_finished"] == 1 and all("platform" in x for x in b["episode_history"])
    repository.restore_all(b); assert fin(10)["is_finished"] and {r["platform"] for r in repository.list_episode_history_since("2000-01-01")} == {"kakao"}
    print("6) 백업/복원 OK")

asyncio.run(main())

# ── 7. 예전 DB(새 열이 없는)를 열면 열이 추가되고 기존 값은 그대로 ──
d = tempfile.mkdtemp(); path = os.path.join(d, "old.db")
con = sqlite3.connect(path)
con.executescript("""CREATE TABLE kakao_webtoons (title_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'excluded', ever_subscribed INTEGER NOT NULL DEFAULT 0,
  thumbnail_url TEXT NOT NULL DEFAULT '', author_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE episode_history (id INTEGER PRIMARY KEY AUTOINCREMENT, title_id TEXT NOT NULL, title_name TEXT NOT NULL, episode_no INTEGER NOT NULL, subtitle TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, error_msg TEXT NOT NULL DEFAULT '', downloaded_at TEXT NOT NULL);
INSERT INTO kakao_webtoons VALUES (1, '옛작품', 'active', 1, '', '', 'x', 'x'); INSERT INTO episode_history (title_id, title_name, episode_no, status, downloaded_at) VALUES ('7', '옛이력', 3, 'success', 'x');""")
con.commit(); con.close()
code = "from app import db, repository; db.get_connection(); w = repository.get_kakao_webtoon(1); h = repository.list_episode_history_since('0')[0]; print(w['status'], w['is_finished'], w['finish_notified'], h['platform'], h['title_name'])"
out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DATABASE_PATH": path, "PYTHONPATH": "."}, capture_output=True, text=True, cwd="/home/claude/wm/webtoon-manager-main")
assert out.stdout.strip().splitlines()[-1] == "active False False naver 옛이력", (out.stdout, out.stderr[-400:])
print("7) 예전 DB 마이그레이션 OK (열 추가, 기존 이력은 네이버로, 값 유지)")
print("\n전부 통과")
