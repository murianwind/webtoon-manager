"""네이버 회차 다운로더(downloader.py) 특성화 테스트 — 이미지 주소 수집(재시도/백오프), 이미지 받기, 회차 하나 받기의 "현재 동작"을 고정한다(이미지 HTTP 오류의 최종 실패 원인은 로그에 남는다).

회차 하나는 "이미지 주소 수집 → 폴더 만들기 → 이미지 동시 받기"이고, 이미지가 하나라도 실패하면 폴더를 통째로 지우고 (False, None)을 돌려준다.
"""
import asyncio
import logging
import tempfile
from pathlib import Path

from app import downloader
from app.constants import DEFAULT_HEADERS, IMAGE_DOWNLOAD_MAX_RETRIES, NAVER_DETAIL_URL_TEMPLATES, RETRY_BACKOFF_BASE_SECONDS
from app.models import EpisodeInfo
from fake_naver_http import FakeResponse, FakeSession, InflightTracker

logs = []
class Capture(logging.Handler):
    def emit(self, record): logs.append(record.getMessage())
logging.getLogger("app.downloader").addHandler(Capture()); logging.getLogger("app.downloader").setLevel(logging.WARNING)
sleeps = []
async def fake_sleep(seconds): sleeps.append(seconds)
downloader.asyncio.sleep = fake_sleep
downloader.random.uniform = lambda a, b: 0.0          # 이미지 재시도 지연의 무작위 흔들림 제거(결정적으로)

EPISODE = EpisodeInfo(episode_no=12, subtitle="열두 번째: 이야기", is_locked=False)
HTML = '<html><body><div class="wt_viewer"><img src="https://img/a.jpg"><img src="https://img/b.png?x=1"><img><img src=""><img src="https://img/c"></div></body></html>'
DETAIL = NAVER_DETAIL_URL_TEMPLATES["webtoon"]
BACKOFF = [RETRY_BACKOFF_BASE_SECONDS * 2 ** i for i in range(IMAGE_DOWNLOAD_MAX_RETRIES)]      # 1, 2, 4, 8, 16초


async def image_urls():
    fetch = lambda handler: downloader._fetch_episode_image_urls(FakeSession(handler), DETAIL, "777", EPISODE, {"c": "v"}, 10)
    # Scenario 1: 상세 페이지의 div.wt_viewer 안 img만, src가 없거나 빈 것은 제외
    session = FakeSession(lambda *a: FakeResponse(200, text=HTML))
    urls = await downloader._fetch_episode_image_urls(session, DETAIL, "777", EPISODE, {"c": "v"}, 10)
    assert urls == ["https://img/a.jpg", "https://img/b.png?x=1", "https://img/c"]
    url, params, headers, cookies = session.calls[0]
    assert (url, params, cookies) == (DETAIL, {"titleId": "777", "no": 12}, {"c": "v"}) and headers == DEFAULT_HEADERS
    # Scenario 2: 뷰어 영역이 없으면 재시도 없이 빈 목록(성공 응답이라 기다리지 않는다)
    sleeps.clear(); assert await fetch(lambda *a: FakeResponse(200, text="<html></html>")) == [] and sleeps == []
    # Scenario 3: 429에 Retry-After(숫자)가 있으면 그 시간만큼, 없거나 숫자가 아니면 백오프(1초, 2초) 기다린 뒤 다시
    sleeps.clear()
    assert await fetch(lambda url, p, n: [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, text=HTML)][n]) == ["https://img/a.jpg", "https://img/b.png?x=1", "https://img/c"]
    assert sleeps == [7.0]
    sleeps.clear()
    await fetch(lambda url, p, n: [FakeResponse(429), FakeResponse(429, headers={"Retry-After": "abc"}), FakeResponse(200, text=HTML)][n])
    assert sleeps == [1.0, 2.0]
    # Scenario 4: 500과 네트워크 예외도 백오프로 다시 시도
    sleeps.clear()
    assert await fetch(lambda url, p, n: [FakeResponse(500), RuntimeError("끊김"), FakeResponse(200, text=HTML)][n]) and sleeps == [1.0, 2.0]
    # Scenario 5: 끝까지 실패하면 (재시도+1)번 시도하고 빈 목록 — 마지막 시도 뒤에는 기다리지 않는다
    sleeps.clear(); dead = FakeSession(lambda *a: FakeResponse(503))
    assert await downloader._fetch_episode_image_urls(dead, DETAIL, "777", EPISODE, {}, 10) == [] and len(dead.calls) == IMAGE_DOWNLOAD_MAX_RETRIES + 1 and sleeps == BACKOFF
    print("1~5) 이미지 주소 수집 OK")


async def single_image():
    tmp = Path(tempfile.mkdtemp())
    # Scenario 6: 200이면 조각(chunk)을 이어 붙여 저장하고(폴더가 없으면 만든다) 크기와 상관없이 성공
    target = tmp / "새 폴더" / "0001.jpg"
    assert await downloader._download_single_image(FakeSession(lambda *a: FakeResponse(200, chunks=[b"abc", b"def"])), "https://img/a.jpg", target, {}, 10) is True
    assert target.read_bytes() == b"abcdef"
    tiny = tmp / "tiny.jpg"
    assert await downloader._download_single_image(FakeSession(lambda *a: FakeResponse(200, chunks=[b"x"])), "u", tiny, {}, 10) is True and tiny.read_bytes() == b"x"
    # Scenario 7: 200이 아니면 백오프(1~16초)로 다시 시도하고 끝까지 안 되면 False — 파일은 만들지 않는다
    sleeps.clear(); dead = FakeSession(lambda *a: FakeResponse(500)); missing = tmp / "no.jpg"
    assert await downloader._download_single_image(dead, "u", missing, {}, 10) is False and len(dead.calls) == IMAGE_DOWNLOAD_MAX_RETRIES + 1 and sleeps == BACKOFF and not missing.exists()
    # 끝내 실패하면 HTTP 상태를 로그에 남긴다(예전에는 예외일 때만 기록해서 HTTP 오류로 실패한 원인을 알 수 없었다)
    assert any("최종 실패" in line and "HTTP 500" in line and "u" in line for line in logs), logs
    # Scenario 8: 네트워크 예외는 재시도하다가 마지막 시도에서 False. 한 번 실패하고 성공하면 True
    sleeps.clear(); boom = FakeSession(lambda *a: RuntimeError("끊김"))
    assert await downloader._download_single_image(boom, "u", tmp / "e.jpg", {}, 10) is False and len(boom.calls) == IMAGE_DOWNLOAD_MAX_RETRIES + 1
    sleeps.clear(); retry = FakeSession(lambda url, p, n: [RuntimeError("끊김"), FakeResponse(200, chunks=[b"ok"])][n])
    assert await downloader._download_single_image(retry, "u", tmp / "r.jpg", {}, 10) is True and sleeps == [1.0] and (tmp / "r.jpg").read_bytes() == b"ok"
    print("6~8) 이미지 하나 받기 OK")


def episode_session(image_handler, html=HTML, tracker=None):
    def handler(url, params, attempt):
        if url.startswith("https://comic.naver.com"):
            return FakeResponse(200, text=html)
        return image_handler(url, attempt, tracker)
    return FakeSession(handler)


async def whole_episode():
    run = lambda session, root, title="작품: 이름", kind="webtoon", workers=2: downloader.download_single_episode(
        session=session, title_id="777", title_name=title, webtoon_type=kind, episode=EPISODE, cookies={"c": "v"}, download_root=str(root),
        folder_zero_fill=4, image_zero_fill=4, max_concurrent_downloads=workers, timeout_seconds=10)
    # Scenario 9: 회차 폴더 "{루트}/{작품 폴더}/[0012] 부제목/0001.jpg…" — 금지 문자는 전각으로, 확장자는 주소에서(없으면 .jpg), 이미지는 동시에 받되 상한을 지킨다
    root = Path(tempfile.mkdtemp()); tracker = InflightTracker()
    many = '<div class="wt_viewer">' + "".join(f'<img src="https://img/{i}.jpg">' for i in range(1, 7)) + "</div>"
    session = episode_session(lambda url, n, tr: FakeResponse(200, chunks=[url.encode()], tracker=tr), html=many, tracker=tracker)
    ok, episode_dir = await run(session, root)
    assert ok is True and episode_dir == root / "작품： 이름" / "[0012] 열두 번째： 이야기"
    assert sorted(p.name for p in episode_dir.iterdir()) == [f"{i:04d}.jpg" for i in range(1, 7)] and (episode_dir / "0003.jpg").read_bytes() == b"https://img/3.jpg"
    assert tracker.max == 2, f"동시 받기 상한(2)을 지켜야 한다: {tracker.max}"
    ok, d = await run(episode_session(lambda url, n, tr: FakeResponse(200, chunks=[b"x"])), Path(tempfile.mkdtemp()))
    assert ok and sorted(p.name for p in d.iterdir()) == ["0001.jpg", "0002.png", "0003.jpg"]           # b.png?x=1 → .png, 확장자 없는 c → .jpg
    # Scenario 10: 웹툰 종류에 맞는 상세 주소(도전만화 등), 모르는 종류는 일반 웹툰 — 요청에는 titleId/no와 쿠키가 실린다
    for kind, expected in (("challenge", NAVER_DETAIL_URL_TEMPLATES["challenge"]), ("bestChallenge", NAVER_DETAIL_URL_TEMPLATES["bestChallenge"]), ("이상한 종류", DETAIL)):
        session = episode_session(lambda url, n, tr: FakeResponse(200, chunks=[b"x"]))
        await run(session, Path(tempfile.mkdtemp()), kind=kind)
        assert session.calls[0][0] == expected and session.calls[0][1] == {"titleId": "777", "no": 12} and session.calls[0][3] == {"c": "v"}
    # Scenario 11: 이미지 주소를 하나도 못 얻으면 (False, None)이고 폴더를 만들지 않는다
    root = Path(tempfile.mkdtemp())
    assert await run(episode_session(lambda *a: FakeResponse(200), html="<html></html>"), root) == (False, None) and list(root.iterdir()) == []
    # Scenario 12: 이미지가 하나라도 끝내 실패하면 그 회차 폴더를 통째로 지우고 (False, None) — 다음 실행이 깨끗한 상태에서 다시 받는다(작품 폴더는 남는다)
    root = Path(tempfile.mkdtemp()); sleeps.clear()
    result = await run(episode_session(lambda url, n, tr: FakeResponse(500) if url.endswith("b.png?x=1") else FakeResponse(200, chunks=[b"x"])), root)
    assert result == (False, None) and (root / "작품： 이름").is_dir() and list((root / "작품： 이름").iterdir()) == []
    print("9~12) 회차 하나 받기 OK")


asyncio.run(image_urls())
asyncio.run(single_image())
asyncio.run(whole_episode())
print("ALL OK")
