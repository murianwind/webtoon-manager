"""카카오페이지 받기 실패의 원인을 로그와 화면 로그에 남긴다 — 이미지 목록 요청이 HTTP 오류나 빈 응답으로 끝나면 예전에는 아무 로그 없이 "받기 실패"만 보였다."""
import asyncio
import logging
import tempfile
from pathlib import Path

from app import db
db.get_connection()
from app import kakao_page_download as kp
from app import kakao_cover
from fake_kakao_rental_site import JPG, R, Site, item

_orig = asyncio.sleep
async def _ns(x): await _orig(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG

logs = []
class Capture(logging.Handler):
    def emit(self, record): logs.append(record.getMessage())
logging.getLogger("app.kakao_page_download").addHandler(Capture()); logging.getLogger("app.kakao_page_download").setLevel(logging.WARNING)
EPISODE = kp.Episode(product_id=15, title="t", number=15, subtitle="14화", is_free=False, accessible=True, page_count=1, hidden=False)


class TextR(R):
    """실제 aiohttp 응답처럼 text()가 있는 가짜 응답(이미지 서버 오류 본문을 읽는 코드가 쓴다)."""
    async def text(self, errors=None): return self._b.decode("utf-8", "replace")


class StatusSite(Site):
    """viewer/data(와 선택적으로 이미지)가 정해진 HTTP 상태로 오는 가짜 서버."""
    def __init__(self, items, viewer_status=200, image_status=200, **k):
        super().__init__(items, **k); self.viewer_status, self.image_status = viewer_status, image_status
    def get(self, url, params=None, headers=None, timeout=None):
        if "viewer/data" in str(url) and self.viewer_status != 200: return R(status=self.viewer_status)
        if "viewer/data" not in str(url) and "/api/" not in str(url) and self.image_status != 200: return TextR(status=self.image_status, body=b"nope")
        return super().get(url, params, headers, timeout)


async def main():
    folder = lambda: Path(tempfile.mkdtemp())
    # Scenario 1: 이미지 목록 요청이 HTTP 403이면 상태를 원인으로 남기고 경고 로그에도 번호와 상태가 나온다
    logs.clear()
    client = kp.KakaoPageClient(StatusSite([item(1, free=True)], viewer_status=403), {}, 10)
    assert await kp.download_episode(client, 1, EPISODE, folder()) is None
    assert client.last_failure and "HTTP 403" in client.last_failure and "이미지 목록" in client.last_failure, client.last_failure
    assert any("product_id=15" in line and "403" in line for line in logs), logs
    # Scenario 2: 200인데 이미지가 없는 응답이면 응답 코드를 원인으로 남긴다
    logs.clear()
    empty = kp.Episode(product_id=5015, title="t", number=15, subtitle="14화", is_free=False, accessible=True, page_count=1, hidden=False)   # 가짜 서버의 item(15) = product_id 5015
    client = kp.KakaoPageClient(Site([item(15)], no_image=(5015,)), {}, 10)     # 가짜 서버: result_code 1만 돌려줌
    assert await kp.download_episode(client, 1, empty, folder()) is None
    assert "비어 있음" in client.last_failure and "result_code=1" in client.last_failure, client.last_failure
    assert any("product_id=5015" in line and "result_code=1" in line for line in logs), logs
    # Scenario 3: 이미지 하나 받기가 HTTP 오류로 끝나면 그 상태가 원인
    client = kp.KakaoPageClient(StatusSite([item(1, free=True)], image_status=404), {}, 10)
    assert await kp.download_episode(client, 1, kp.Episode(product_id=5001, title="t", number=1, subtitle="1화", is_free=True, accessible=True, page_count=1, hidden=False), folder()) is None
    assert "이미지 받기" in client.last_failure and "404" in client.last_failure, client.last_failure
    # Scenario 4: 성공하면 이전 실패 원인이 남지 않는다
    client = kp.KakaoPageClient(Site([item(1, free=True)]), {}, 10); client.last_failure = "오래된 원인"
    assert await kp.download_episode(client, 1, kp.Episode(product_id=5001, title="t", number=1, subtitle="1화", is_free=True, accessible=True, page_count=1, hidden=False), folder()) is not None
    assert client.last_failure is None
    # Scenario 5: 자동 다운로드의 화면 로그 줄에 원인이 붙는다
    lines = []
    site = Site([item(1, free=True), item(2, free=True)], no_image=(5001,))
    r = await kp.run_download(kp.KakaoPageClient(site, {}, 10), series_id=1, title=None, download_root=str(folder()), max_episodes=5, on_progress=lines.append)
    stop = next(line for line in lines if "받기 실패" in line)
    assert "1번 회차 받기 실패" in stop and "여기서 멈춥니다" in stop and "원인: 이미지 목록이 비어 있음(result_code=1)" in stop, stop
    assert r.failed == 1
    print("ALL OK")

asyncio.run(main())
