"""마지막으로 받은 회차 찾기(folder_scanner.py) 특성화 테스트 — zip 파일 이름 속 부제목을 사이트 회차 목록과 맞춰 실제 회차 번호를 찾는 현재 동작을 고정한다.

zip 맨 앞 숫자는 끊김 없는 일련번호일 뿐 네이버 회차 번호가 아니라서 쓰지 않고, 파일 이름에 남은 부제목으로 위치를 찾는다.
"""
import tempfile
from pathlib import Path

from app import folder_scanner
from app.models import EpisodeInfo

EPISODES = [EpisodeInfo(1, "1화 시작", False), EpisodeInfo(2, "2화: 두 번째", False), EpisodeInfo(10, "[10화] 열 번째 이야기!", False), EpisodeInfo(11, "11화 마지막", False)]


def folder(*names):
    path = Path(tempfile.mkdtemp())
    for name in names: (path / name).write_bytes(b"x")
    return path


find = folder_scanner.find_last_downloaded_episode_no
# Scenario 1: 정규화 — 한글/영문/숫자만 남겨서 비교하고(공백과 특수문자 차이 무시), 앞의 일련번호 부분은 떼고 비교한다
assert folder_scanner._normalize_for_match("2화: 두 번째!") == "2화두번째" and folder_scanner._normalize_for_match(None) == ""
# Scenario 2: 폴더가 아니거나 회차 목록이 비었거나 zip이 없으면 0(호출자는 DB에 저장된 값을 그대로 쓴다)
assert find(Path(tempfile.mkdtemp()) / "없음", EPISODES) == 0 and find(folder("a.zip"), []) == 0 and find(folder("메모.txt"), EPISODES) == 0
# Scenario 3: 일련번호가 가장 큰 zip을 "마지막"으로 보고, 그 파일 이름 속 부제목과 맞는 회차 번호를 돌려준다(일련번호 109가 회차 번호가 아니다)
assert find(folder("001 작품 1화 시작.zip", "109 작품 2화 두 번째.zip"), EPISODES) == 2
# Scenario 4: 특수문자/공백이 달라도 맞는다
assert find(folder("050 작품 10화 열 번째 이야기.zip"), EPISODES) == 10
# Scenario 5: 부제목이 여러 회차에 포함되면 번호가 가장 큰 회차를 고른다(최신부터 검사)
assert find(folder("003 작품 11화 마지막 1화 시작.zip"), EPISODES) == 11
# Scenario 6: 맞는 회차가 없거나 이름이 비교할 글자로 비면 0
assert find(folder("010 작품 전혀 다른 제목.zip"), EPISODES) == 0 and find(folder("010.zip"), EPISODES) == 0 and find(folder("010 !!!.zip"), EPISODES) == 0
# Scenario 7: 일련번호가 없는 zip은 가장 앞으로 밀리고(번호 있는 zip이 마지막), 확장자 대소문자는 구분하지 않는다
assert find(folder("메모 11화 마지막.zip", "002 작품 1화 시작.ZIP"), EPISODES) == 1
assert folder_scanner._latest_zip_filename(Path(tempfile.mkdtemp())) is None
print("ALL OK")
