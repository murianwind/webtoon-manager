"""대여권 자동 사용 — 설정(기본 꺼짐), 자동 다운로드 연결, 사용 알림.

설정이 꺼져 있으면(기본) 예전과 똑같이 동작한다. 켜면 구독 중인 작품에서 기다무로 못 여는 회차를 그 작품의 보유 대여권 수까지 열고,
쓸 때마다 디스코드로 알린다(남은 장수 포함). 더 못 열면 기존 "대여권 충전이 필요합니다" 알림이 간다.
"""
import asyncio
import json
import tempfile
import time

import aiohttp
import httpx

from app import db, discord_config, discord_notify, job_status, repository, scheduler
from app import kakao_page_auth as auth
from app import kakao_page_download as kp
from app.config import get_settings
from fake_kakao_rental_site import JPG, SITE, FakeSession, Site, item
db.get_connection()
import app.main as m

aiohttp.ClientSession = FakeSession
_o = asyncio.sleep
async def _ns(x): await _o(0)
kp.asyncio.sleep = _ns; scheduler.asyncio.sleep = _ns
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG

sent, state = [], {"ok": True}
async def fake_send(session, settings, message):
    if not discord_config.get_webhook_url() or not state["ok"]: return False
    sent.append(message); return True
discord_notify.send_webhook_notification = fake_send

use_posts = lambda site: [p for p in site.posts if p[0].endswith("ticket/use")]
summary = lambda: [line for line in (job_status.snapshot().get("download") or {}).get("log", []) if "다운로드 요약" in line][-1].split(" — ", 1)[1]   # 앞의 시각 제거


async def setting_api():
    # Scenario 1: 설정은 기본 꺼짐이고, 설정 API로 켜고 끈다
    assert repository.is_kakao_rental_ticket_auto_use_enabled() is False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        assert (await c.get("/api/settings/kakao-rental-ticket-auto-use")).json() == {"enabled": False}
        assert (await c.post("/api/settings/kakao-rental-ticket-auto-use", json={"enabled": True})).json() == {"enabled": True}
        assert repository.is_kakao_rental_ticket_auto_use_enabled() is True
        assert (await c.post("/api/settings/kakao-rental-ticket-auto-use", json={"enabled": False})).json() == {"enabled": False}
    assert repository.is_kakao_rental_ticket_auto_use_enabled() is False
    print("1) 설정 API OK (기본 꺼짐)")


async def notice_message():
    # Scenario 2: 사용 알림 문구 — 남은 장수 포함, 대상이 없으면 보내지 않는다
    sent.clear(); discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    wt = {"title": "블루엔젤", "title_id": 9}
    base = dict(downloaded=[2, 3], downloaded_items=[(2, "a"), (3, "b")], failed=None, plan=kp.DownloadPlan(mode="compare"), title="블루엔젤")
    await scheduler._notify_kakao_rental_used(None, None, wt, kp.RunResult(**base, rental_used=[2, 3], rental_remaining=1))
    await scheduler._notify_kakao_rental_used(None, None, wt, kp.RunResult(**base, rental_used=[7], rental_remaining=None))
    await scheduler._notify_kakao_rental_used(None, None, wt, kp.RunResult(**base, rental_used=[], rental_remaining=3))
    assert sent == ["[블루엔젤] 대여권 2장을 사용했습니다 (2번, 3번). 남은 대여권 1장", "[블루엔젤] 대여권 1장을 사용했습니다 (7번)."], sent
    # 전송이 실패해도 예외 없이 지나간다(다운로드는 이미 끝났다)
    state["ok"] = False
    await scheduler._notify_kakao_rental_used(None, None, wt, kp.RunResult(**base, rental_used=[2], rental_remaining=0))
    state["ok"] = True
    print("2) 사용 알림 문구 OK")


def remaining_free_counts_only_free_downloads():
    # Scenario 2-2: 이어받기 판단(_kakao_remaining_free)은 기다무/대여권으로 연 회차를 "받을 수 있던 회차"로 세지 않는다
    ep = lambda n: kp.Episode(product_id=n, title=f"{n}화", number=n, subtitle=f"{n}화", is_free=True, accessible=True, page_count=2, hidden=False)
    plan = kp.DownloadPlan(mode="compare", to_download=[ep(1), ep(2), ep(3)], locked=[ep(7), ep(8)])
    r = kp.RunResult([1, 2, 7, 8], [(1, "1화"), (2, "2화"), (7, "7화"), (8, "8화")], None, plan, rental_used=[7, 8])
    assert scheduler._kakao_remaining_free(r) == 1, scheduler._kakao_remaining_free(r)       # 무료로 받은 건 2개뿐 → 3번이 남았다
    r = kp.RunResult([1, 2, 3, 7], [], None, plan, ticket_used=7)
    assert scheduler._kakao_remaining_free(r) == 0
    print("2-2) 이어받기 판단 OK")


async def auto_flow():
    settings = get_settings(); root = tempfile.mkdtemp()
    repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, root)
    auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": "v", "path": "/", "expirationDate": time.time() + 25 * 86400} for n in auth.REQUIRED_COOKIES])))
    discord_config.set_webhook_url("https://discord.com/api/webhooks/1/x")
    job_status.start("download")
    repository.upsert_new_kakao_webtoon(1, "작품", status=repository.STATUS_ACTIVE)

    # Scenario 3: 설정이 꺼져 있으면 보유 대여권이 있어도 쓰지 않고, 기존 "대여권 충전이 필요합니다" 알림만 간다
    sent.clear()
    SITE["site"] = site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2)
    await scheduler._download_kakao_subscriptions(settings, [])
    assert use_posts(site) == [] and site.rental == 2
    assert sent == ["[작품] 다음 회차 (2번 '2화')부터는 대여권 충전이 필요합니다."], sent
    assert summary() == "카카오페이지 다운로드 요약 — 작품 1개 / 받음 1회차 / 대여권이 필요해 멈춘 작품 1개 / 기다무 충전을 기다리는 작품 0개 / 오류 0개", summary()
    print("3) 설정 꺼짐: 대여권을 쓰지 않음 OK")

    # Scenario 4: 설정을 켜면 보유 2장까지 번호순으로 쓰고, 쓴 내용을 알리고, 못 연 다음 회차는 충전 필요로 알린다
    sent.clear(); repository.set_setting("kakao_rental_ticket_auto_use", "1")
    SITE["site"] = site = Site([item(1, free=True), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2)
    site.opened.clear()
    await scheduler._download_kakao_subscriptions(settings, [])
    assert [p[1] for p in use_posts(site)] == [{"product_id": 5002, "ticket_type": "RT01"}, {"product_id": 5003, "ticket_type": "RT01"}], site.posts
    assert sent == ["[작품] 대여권 2장을 사용했습니다 (2번, 3번). 남은 대여권 0장", "[작품] 다음 회차 (4번 '4화')부터는 대여권 충전이 필요합니다."], sent
    assert summary() == "카카오페이지 다운로드 요약 — 작품 1개 / 받음 2회차 / 대여권 2장 사용 / 대여권이 필요해 멈춘 작품 1개 / 기다무 충전을 기다리는 작품 0개 / 오류 0개", summary()
    print("4) 설정 켜짐: 보유 수까지 사용 + 사용 알림 + 충전 필요 알림 + 요약 OK")

    # Scenario 5: 다시 실행해도 같은 회차에 또 쓰지 않는다(이미 받았거나 보유 0장) — 알림도 중복되지 않는다
    sent.clear(); before = len(use_posts(site))
    await scheduler._download_kakao_subscriptions(settings, [])
    assert len(use_posts(site)) == before and sent == [], (use_posts(site), sent)
    print("5) 재실행 시 중복 사용/알림 없음 OK")


asyncio.run(setting_api())
asyncio.run(notice_message())
remaining_free_counts_only_free_downloads()
asyncio.run(auto_flow())
print("ALL OK")
