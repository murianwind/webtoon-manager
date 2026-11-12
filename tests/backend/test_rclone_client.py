"""rclone 래퍼(app/rclone_client.py) — rclone 프로그램을 부르는 경계에서 "어떤 명령줄을 만드는지, 결과를 어떻게 읽는지, 실패를 어떻게 다루는지"를 고정한다.
subprocess.run을 가짜로 바꿔서 실제 rclone 없이 확인한다(archiver 쪽 테스트는 이 래퍼 위의 FakeRclone을 쓰므로, 명령줄 자체는 여기서만 확인된다).
"""
import json
import subprocess
import tempfile
from pathlib import Path

from app import rclone_client as rc

CONFIG = "/cfg/rclone.conf"
calls = []; outcome = {"stdout": "", "stderr": "", "code": 0, "raise": None}
class Done:
    def __init__(self): self.stdout, self.stderr, self.returncode = outcome["stdout"], outcome["stderr"], outcome["code"]
script = []   # 비어 있지 않으면 명령 하나마다 (stdout, stderr, code, 예외)를 차례로 쓴다 — 여러 명령이 이어지는 동작(복사 → 크기 확인 → 원본 삭제)을 본다
class ScriptDone:
    def __init__(self, stdout, stderr, code): self.stdout, self.stderr, self.returncode = stdout, stderr, code
def fake_run(cmd, **kwargs):
    calls.append((cmd, kwargs))
    if script:
        stdout, stderr, code, exc = script.pop(0)
        if exc: raise exc
        return ScriptDone(stdout, stderr, code)
    if outcome["raise"]: raise outcome["raise"]
    return Done()
ok_ = lambda stdout="": (stdout, "", 0, None)
bad_ = lambda stderr="fail", exc=None: ("", stderr, 1, exc)
def listing(*entries): return ok_(json.dumps([{"Name": n, "Size": s, "IsDir": False} for n, s in entries]))
rc.subprocess.run = fake_run
def respond(stdout="", stderr="", code=0, raise_=None): outcome.update(stdout=stdout, stderr=stderr, code=code); outcome["raise"] = raise_; calls.clear()
last = lambda: calls[-1]
def raises(fn, *args):
    try: fn(*args)
    except rc.RcloneError as e: return str(e)
    raise AssertionError("RcloneError가 나야 한다")

# Scenario 1: 실행 — 명령줄은 rclone --config 설정파일 명령..., 출력은 텍스트로, 기본 30초 제한. 실패는 RcloneError(오류 문구를 그대로, 없으면 종료코드)
respond("ok"); assert rc._run(CONFIG, ["listremotes"]) == "ok"
cmd, kw = last(); assert cmd == ["rclone", "--config", CONFIG, "listremotes"] and kw["capture_output"] is True and kw["text"] is True and kw["timeout"] == 30, (cmd, kw)
respond(code=3, stderr="  auth failed \n"); assert raises(rc._run, CONFIG, ["x"]) == "auth failed"
respond(code=7); assert "종료코드 7" in raises(rc._run, CONFIG, ["x"])
respond(raise_=FileNotFoundError()); assert "rclone 프로그램을 찾을 수 없습니다" in raises(rc._run, CONFIG, ["x"])
respond(raise_=subprocess.TimeoutExpired("rclone", 30)); message = raises(rc._run, CONFIG, ["x"]); assert "30초" in message
respond(raise_=subprocess.TimeoutExpired("rclone", 5))
try: rc._run(CONFIG, ["x"], 5)
except rc.RcloneError as e: assert "5초" in str(e), str(e)                                                                              # 지정한 제한 시간이 문구에 들어간다
print("1) 실행/오류 OK")

# Scenario 2: 원격 목록 — 콜론을 떼고 빈 줄은 건너뛴다
respond("onedrive:\n\n gdrive: \n"); assert rc.list_remotes(CONFIG) == ["onedrive", "gdrive"] and last()[0][-1] == "listremotes"

# Scenario 3: 폴더 목록 — 폴더만(--dirs-only), 경로가 있으면 remote:경로, 없으면 remote:. 안 내용은 들여다보지 않고 이름과 상대경로만
respond(json.dumps([{"Name": "작품A"}, {"Name": "작품B"}])); assert rc.list_folders(CONFIG, "od", "웹툰/완결") == [{"name": "작품A", "path": "웹툰/완결/작품A"}, {"name": "작품B", "path": "웹툰/완결/작품B"}]
assert last()[0][3:] == ["lsjson", "od:웹툰/완결", "--dirs-only"]
respond(json.dumps([{"Name": "루트폴더"}])); assert rc.list_folders(CONFIG, "od", "") == [{"name": "루트폴더", "path": "루트폴더"}] and last()[0][4] == "od:"
respond(code=1, stderr="no such remote"); assert "no such remote" in raises(rc.list_folders, CONFIG, "od", "")
print("2~3) 원격/폴더 목록 OK")

# Scenario 4: 폴더가 비었는지 — 비면 True, 하나라도 있으면 False, 확인 실패는 안전하게 False(선택 불가 처리)
respond("[]"); assert rc.is_folder_empty(CONFIG, "od", "a/b") is True and last()[0][3:] == ["lsjson", "od:a/b"]
respond(json.dumps([{"Name": "1.zip"}])); assert rc.is_folder_empty(CONFIG, "od", "") is False and last()[0][4] == "od:"
respond(code=1, stderr="timeout"); assert rc.is_folder_empty(CONFIG, "od", "a") is False
print("4) 폴더 비었는지 OK")

# Scenario 5: 폴더 만들기/파일 올리기 — mkdir remote:경로, moveto는 업로드용 긴 제한(1800초). 경로가 없으면 remote:파일명
respond(); rc.create_folder(CONFIG, "od", "a/b"); assert last()[0][3:] == ["mkdir", "od:a/b"]
# (파일 올리기는 복사 → 올라간 크기 확인 → 원본 삭제 순서다 — 아래 Scenario 8에서 본다. rclone moveto로 한 번에 옮기지 않는다)
respond(); rc.moveto(CONFIG, "od:a.zip", "/work/a.zip"); cmd, kw = last(); assert cmd[3:] == ["moveto", "od:a.zip", "/work/a.zip"] and kw["timeout"] == 1800
respond(); rc.copyto(CONFIG, "/work/i.xml", "od:a/i.xml"); cmd, kw = last(); assert cmd[3:] == ["copyto", "/work/i.xml", "od:a/i.xml"] and kw["timeout"] == 1800        # 복사는 원본을 지우지 않는다
respond(); rc.delete_file(CONFIG, "od", "a", "cover.jpg"); assert last()[0][3:] == ["deletefile", "od:a/cover.jpg"]
respond(); rc.delete_file(CONFIG, "od", "", "cover.jpg"); assert last()[0][-1] == "od:cover.jpg"
print("5) 만들기/올리기/지우기 OK")

# Scenario 6: 파일 존재/목록 — 있으면 True, 없거나 확인 실패면 False. 재귀 목록은 파일만 상대경로로(--recursive --files-only), 실패는 대상을 밝힌 RcloneError. 최상위 파일 목록은 이름만
respond(json.dumps([{"Name": "a.zip"}, {"Name": "b.zip"}])); assert rc.file_exists(CONFIG, "od", "x", "b.zip") is True and rc.file_exists(CONFIG, "od", "x", "c.zip") is False
respond(code=1); assert rc.file_exists(CONFIG, "od", "x", "a.zip") is False
respond(json.dumps([{"Path": "sub/1.zip"}, {"Path": "2.zip"}])); assert rc.list_files_recursive(CONFIG, "od", "w") == ["sub/1.zip", "2.zip"] and last()[0][3:] == ["lsjson", "od:w", "--recursive", "--files-only"]
respond(code=1, stderr="denied"); message = raises(rc.list_files_recursive, CONFIG, "od", "w"); assert "'od:w' 목록을 읽을 수 없습니다" in message and "denied" in message
respond(json.dumps([{"Name": "1.zip"}, {"Name": "2.zip"}])); assert rc.list_top_level_files(CONFIG, "od", "") == ["1.zip", "2.zip"] and last()[0][3:] == ["lsjson", "od:", "--files-only"]
respond(code=1, stderr="x"); assert "'od:a' 목록을 읽을 수 없습니다" in raises(rc.list_top_level_files, CONFIG, "od", "a")
print("6) 존재/목록 OK")

# Scenario 7: 빈 폴더 정리 — 루트는 건드리지 않고(명령 없음), 실패해도 예외 없이 지나간다. 작은 텍스트 파일 읽기는 내용을 돌려주고 없거나 실패하면 None
respond(); rc.rmdirs_if_empty(CONFIG, "od", ""); assert calls == []
respond(); rc.rmdirs_if_empty(CONFIG, "od", "a/b"); assert last()[0][3:] == ["rmdirs", "od:a/b"]
respond(code=1, stderr="busy"); rc.rmdirs_if_empty(CONFIG, "od", "a/b")                                                                      # 예외 없음
respond("<xml/>"); assert rc.read_small_text_file(CONFIG, "od", "작품", "info.xml") == "<xml/>" and last()[0][3:] == ["cat", "od:작품/info.xml"]
respond("<xml/>"); rc.read_small_text_file(CONFIG, "od", "", "info.xml"); assert last()[0][-1] == "od:info.xml"
respond(code=1, stderr="not found"); assert rc.read_small_text_file(CONFIG, "od", "작품", "info.xml") is None

# Scenario 8: 파일 크기 — 목록에서 그 이름의 Size를 읽는다(없으면 None). 목록을 못 읽으면 RcloneError(호출부가 "알 수 없음"으로 다룬다)
script[:] = [listing(("a.zip", 10), ("b.zip", 0))]; assert rc.file_size(CONFIG, "od", "x", "b.zip") == 0 and last()[0][3:] == ["lsjson", "od:x"]
script[:] = [listing(("a.zip", 10))]; assert rc.file_size(CONFIG, "od", "x", "c.zip") is None
script[:] = [bad_("denied")]; assert "denied" in raises(rc.file_size, CONFIG, "od", "x", "a.zip")
script[:] = [ok_(json.dumps([{"Name": "a.zip"}]))]; assert rc.file_size(CONFIG, "od", "x", "a.zip") is None                                       # 크기 정보가 없으면 모른다(0으로 보지 않는다)
print("8) 파일 크기 OK")

# Scenario 9: **원본 보호** — 올리기는 복사만 하고(moveto 금지), 올라간 파일의 크기가 원본과 같다는 것을 확인한 뒤에만 원본을 지운다. 어떤 실패에서도 원본은 그대로다
work = Path(tempfile.mkdtemp()); data = b"x" * 10
def make_local(content=data):
    f = work / "1.zip"; f.write_bytes(content); return f
def upload(f): rc.move_file_to_remote(CONFIG, str(f), "od", "웹툰/작품", "0001.zip")
def names_of(): return [c[0][3] for c in calls]
f = make_local(); script[:] = [ok_(), listing(("0001.zip", 10))]; calls.clear(); upload(f)                                         # 성공: 복사 → 크기 확인 → 원본 삭제
assert names_of() == ["copyto", "lsjson"] and calls[0][0][3:] == ["copyto", str(f), "od:웹툰/작품/0001.zip"] and calls[0][1]["timeout"] == 1800 and calls[1][0][3:] == ["lsjson", "od:웹툰/작품"], calls
assert not f.exists(), "확인된 뒤에는 원본이 지워진다(이동)"
f = make_local(); script[:] = [ok_(), listing(("0001.zip", 10))]; rc.move_file_to_remote(CONFIG, str(f), "od", "", "0001.zip"); assert calls[-2][0][-1] == "od:0001.zip" and calls[-1][0][3:] == ["lsjson", "od:"]   # 경로 없으면 루트
f = make_local(); script[:] = [bad_("401 Unauthorized"), listing(("0001.zip", 0))]; calls.clear()                                  # 복사 실패(0KB 자리표시 파일만 남음) → 실패, 원본 그대로
assert "401" in raises(upload, f) and f.read_bytes() == data and "moveto" not in names_of(), (names_of(), f.exists())
f = make_local(); script[:] = [bad_("timeout"), listing()]; assert "timeout" in raises(upload, f) and f.read_bytes() == data                # 복사 실패 + 목적지에 없음 → 실패, 원본 그대로
f = make_local(); script[:] = [bad_("late timeout"), listing(("0001.zip", 10))]; upload(f); assert not f.exists(), "복사는 실패로 끝났지만 올라간 크기가 같으면 올라간 것이다(응답만 늦은 경우) — 성공 처리"
f = make_local(); script[:] = [bad_("late timeout"), bad_("denied")]; assert "late timeout" in raises(upload, f) and f.read_bytes() == data     # 복사 실패 + 확인도 못 함 → 실패, 원본 그대로
f = make_local(); script[:] = [ok_(), listing(("0001.zip", 7))]; message = raises(upload, f); assert "크기" in message and f.read_bytes() == data      # 크기가 다르면 → 실패, 원본 그대로
f = make_local(); script[:] = [ok_(), listing(("0001.zip", 0))]; assert "크기" in raises(upload, f) and f.read_bytes() == data                     # 0KB로 올라갔다면 → 실패
f = make_local(); script[:] = [ok_(), bad_("denied")]; assert "denied" in raises(upload, f) and f.read_bytes() == data                            # 올라간 파일을 확인하지 못하면 → 실패, 원본 그대로
f = make_local(); script[:] = [ok_(), listing()]; assert "크기" in raises(upload, f) and f.read_bytes() == data                                  # 복사는 끝났다는데 목적지에 없으면 → 실패
f = make_local(b""); script[:] = [ok_()]; calls.clear(); assert "0KB" in raises(upload, f) and calls == [] and f.exists(), "원본이 0KB면 아무것도 하지 않고 거절"     # 깨진 다운로드를 옮겨서 원본까지 없애지 않는다
f = work / "없는파일.zip"; script[:] = [ok_()]; calls.clear(); assert "원본" in raises(upload, f) and calls == []
script.clear()
print("9) 원본 보호 OK")

# Scenario 10: 0KB 찌꺼기 하나 지우기 — 크기가 정확히 0이라고 확인될 때만. 크기가 있거나 없거나 확인 실패면 지우지 않고 False
script[:] = [listing(("a.zip", 0)), ok_()]; calls.clear(); assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is True and names_of() == ["lsjson", "deletefile"] and calls[1][0][-1] == "od:x/a.zip"
script[:] = [listing(("a.zip", 5))]; calls.clear(); assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is False and names_of() == ["lsjson"], "크기가 있으면 안 지운다"
script[:] = [listing(("b.zip", 0))]; calls.clear(); assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is False and names_of() == ["lsjson"], "없으면 안 지운다"
script[:] = [bad_("denied")]; calls.clear(); assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is False and names_of() == ["lsjson"], "크기를 모르면 안 지운다"
script[:] = [ok_(json.dumps([{"Name": "a.zip"}]))]; calls.clear(); assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is False and names_of() == ["lsjson"], "크기 정보가 없으면 안 지운다"
script[:] = [listing(("a.zip", 0)), bad_("401")]; assert rc.delete_empty_file(CONFIG, "od", "x", "a.zip") is False, "지우기가 실패해도 예외 없이 False"
script.clear()
print("10) 0KB 하나 지우기 OK")

# Scenario 11: 폴더의 0KB .zip 정리 — 폴더 안 바로 아래의 파일 중 크기가 0인 .zip만(대소문자 무시). 크기가 있는 파일, .zip 아닌 파일, 폴더는 그대로. 지운 이름을 돌려준다. 목록 실패면 아무것도 안 지운다
entries = json.dumps([{"Name": "z1.zip", "Size": 0, "IsDir": False}, {"Name": "z2 (2).ZIP", "Size": 0, "IsDir": False}, {"Name": "real.zip", "Size": 9, "IsDir": False},
                      {"Name": "memo.txt", "Size": 0, "IsDir": False}, {"Name": "sub.zip", "Size": 0, "IsDir": True}, {"Name": "nosize.zip", "IsDir": False}])
script[:] = [ok_(entries), ok_(), ok_()]; calls.clear(); assert rc.purge_empty_zip_files(CONFIG, "od", "x") == ["z1.zip", "z2 (2).ZIP"]
assert names_of() == ["lsjson", "deletefile", "deletefile"] and [c[0][-1] for c in calls[1:]] == ["od:x/z1.zip", "od:x/z2 (2).ZIP"] and calls[0][0][3:] == ["lsjson", "od:x", "--files-only"], calls
script[:] = [ok_(entries), bad_("401"), ok_()]; assert rc.purge_empty_zip_files(CONFIG, "od", "x") == ["z2 (2).ZIP"], "지우기가 실패한 것은 빼고 나머지는 계속"
script[:] = [bad_("denied")]; calls.clear(); assert rc.purge_empty_zip_files(CONFIG, "od", "x") == [] and names_of() == ["lsjson"]
script[:] = [ok_("[]")]; calls.clear(); assert rc.purge_empty_zip_files(CONFIG, "od", "") == [] and calls[0][0][-2:] == ["od:", "--files-only"]
script.clear()
print("11) 폴더 0KB .zip 정리 OK")

# Scenario 12: 존재 확인에서 0KB를 빼기 — ignore_empty=True면 크기 0은 없는 것으로 본다(크기를 모르면 있는 것으로 본다). 기본값은 예전과 같다
script[:] = [listing(("a.zip", 0))]; assert rc.file_exists(CONFIG, "od", "x", "a.zip") is True
script[:] = [listing(("a.zip", 0))]; assert rc.file_exists(CONFIG, "od", "x", "a.zip", ignore_empty=True) is False
script[:] = [listing(("a.zip", 3))]; assert rc.file_exists(CONFIG, "od", "x", "a.zip", ignore_empty=True) is True
script[:] = [ok_(json.dumps([{"Name": "a.zip"}]))]; assert rc.file_exists(CONFIG, "od", "x", "a.zip", ignore_empty=True) is True
script.clear()
print("12) 존재 확인 0KB 제외 OK")
print("ALL OK")
