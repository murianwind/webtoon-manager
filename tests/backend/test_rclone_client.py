"""rclone 래퍼(app/rclone_client.py) — rclone 프로그램을 부르는 경계에서 "어떤 명령줄을 만드는지, 결과를 어떻게 읽는지, 실패를 어떻게 다루는지"를 고정한다.
subprocess.run을 가짜로 바꿔서 실제 rclone 없이 확인한다(archiver 쪽 테스트는 이 래퍼 위의 FakeRclone을 쓰므로, 명령줄 자체는 여기서만 확인된다).
"""
import json
import subprocess

from app import rclone_client as rc

CONFIG = "/cfg/rclone.conf"
calls = []; outcome = {"stdout": "", "stderr": "", "code": 0, "raise": None}
class Done:
    def __init__(self): self.stdout, self.stderr, self.returncode = outcome["stdout"], outcome["stderr"], outcome["code"]
def fake_run(cmd, **kwargs):
    calls.append((cmd, kwargs))
    if outcome["raise"]: raise outcome["raise"]
    return Done()
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
respond(); rc.move_file_to_remote(CONFIG, "/work/1.zip", "od", "웹툰/작품", "0001.zip"); cmd, kw = last(); assert cmd[3:] == ["moveto", "/work/1.zip", "od:웹툰/작품/0001.zip"] and kw["timeout"] == 1800
respond(); rc.move_file_to_remote(CONFIG, "/work/1.zip", "od", "", "0001.zip"); assert last()[0][-1] == "od:0001.zip"
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
print("ALL OK")
