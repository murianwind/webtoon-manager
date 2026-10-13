"""카카오페이지 자동 다운로드 — 구독 중인 작품을 돌며 받고(번호순), 대여권/기다무/다 받음 알림과 요약 로그, 완결 알림.
"""

import asyncio
import logging

import aiohttp

from app import discord_bot, discord_notify, download_pacing, download_roots, job_status, kakao_page_auth, kakao_page_download, repository

log = logging.getLogger(__name__)

# 카카오페이지 자동 다운로드는 네이버와 같은 설정(작품당 상한 max_new_episodes_per_title, 배치 사이 쉬는 시간 batch_rest_minutes)을
# 쓴다. 상한 0 이하는 "제한 없음"이라 한 번에 전부 받는다(아래 값은 그 경우의 요청당 상한일 뿐이다).
_KAKAO_UNLIMITED_EPISODES = 100_000


# 비정상 응답(계속 "받을 게 남았다"고만 하는 경우)으로 한 작품에서 무한히 도는 걸 막는 안전장치 — 상한 10이면 2000회차까지.
_KAKAO_MAX_BATCHES_PER_TITLE = 200


def _kakao_remaining_free(result) -> int:
    """이번 배치 뒤에도 받을 수 있는 회차가 몇 개 남았는지(상한에 걸려 못 받은 것). 기다무로 연 회차는 "받을 회차"가 아니라 잠긴 회차였으므로
    받은 개수에서 뺀다(대여권으로 연 회차도 마찬가지). 오류/실패가 있으면 0 — 그 작품은 여기서 멈춘다."""
    if result.error is not None or result.failed is not None:
        return 0
    downloaded_free = len(result.downloaded) - (1 if result.ticket_used is not None else 0) - len(result.rental_used)
    return max(0, len(result.plan.to_download) - downloaded_free)


def _record_kakao_batch(webtoon: dict, result, failures: list[dict]) -> None:
    """한 배치의 결과를 이력/로그/실패 목록에 남긴다."""
    title, series_id = webtoon["title"], str(webtoon["title_id"])
    for number, subtitle in result.downloaded_items:
        repository.add_episode_history(series_id, result.title, number, subtitle, "success", platform="kakao")
    if result.error:
        job_status.log_line("download", f"[{title}] {result.error}")
    elif result.failed is not None:
        repository.add_episode_history(series_id, result.title, result.failed, "", "failed", "이미지 받기 실패", platform="kakao")
        failures.append({"title_name": result.title, "episode_no": result.failed, "subtitle": "이미지 받기 실패"})
    elif not result.downloaded and result.plan.marker is not None and result.plan.marker.warning:
        job_status.log_line("download", f"[{title}] ⚠ 폴더의 파일(표식)이 사이트 회차와 맞지 않아 번호대로 이어받았습니다")


async def _download_kakao_title(client, settings, webtoon: dict, root: str, failures: list[dict], *, use_rental_tickets: bool = False, session=None):
    """작품 하나를 받는다. 상한(작품당 한 번에 받는 수)에 걸려 받을 회차가 남으면, 네이버와 같이 batch_rest_minutes만큼 쉬었다가 같은
    실행 안에서 이어서 받는다(다음 정기 실행까지 기다리지 않는다). 실패/오류가 나면 거기서 멈춘다. 마지막 배치의 결과를 돌려준다(예외로
    중단되면 None) — 완결 판정은 이 결과로 한다. use_rental_tickets면 기다무로 못 여는 회차를 이 작품의 보유 대여권으로 열고, 배치마다
    쓴 내용을 session의 웹훅으로 알린다(여러 배치에 걸쳐 쓰면 배치마다 한 번씩)."""
    title, series_id = webtoon["title"], webtoon["title_id"]
    result = None
    for batch in range(1, _KAKAO_MAX_BATCHES_PER_TITLE + 1):
        cap, rest_minutes = download_pacing.limits(settings)  # 설정 화면에서 바꾼 값이 있으면 그 값(배치마다 다시 읽어서 재시작 없이 적용)
        max_episodes = cap if cap > 0 else _KAKAO_UNLIMITED_EPISODES
        try:
            result = await kakao_page_download.run_download(
                client, series_id=series_id, title=None, download_root=root, max_episodes=max_episodes,
                on_progress=lambda line, t=title: job_status.log_line("download", f"[{t}] {line}"),
                use_rental_tickets=use_rental_tickets,
            )
        except Exception as e:
            log.error("카카오페이지 웹툰(series_id=%s) 다운로드 중 예외 — 다음으로 진행: %s", series_id, e)
            job_status.log_line("download", f"[{title}] 처리 중 오류: {e}")
            failures.append({"title_name": title, "episode_no": None, "subtitle": str(e)})
            return None
        _record_kakao_batch(webtoon, result, failures)
        if session is not None:
            await _notify_kakao_rental_used(session, settings, webtoon, result)
        remaining = _kakao_remaining_free(result)
        if remaining <= 0:
            break
        if batch == _KAKAO_MAX_BATCHES_PER_TITLE:
            job_status.log_line("download", f"[{title}] 한 번에 받을 배치가 너무 많아 여기서 멈춥니다(남은 {remaining}화는 다음 실행에서)")
            break
        job_status.log_line("download", f"[{title}] {len(result.downloaded)}화 받음, 남은 {remaining}화는 {rest_minutes:g}분 쉬었다가 이어받기")
        await asyncio.sleep(rest_minutes * 60)
    return result


async def _notify_kakao_rental_used(session, settings, webtoon: dict, result) -> None:
    """자동으로 대여권을 썼으면 쓴 회차와 이 작품에 남은 장수를 디스코드로 알린다(돈을 낸 자원이 자동으로 쓰였으니 매번 알린다).
    알림이 실패해도 다운로드는 이미 끝났으므로 로그만 남기고 넘어간다."""
    if not result.rental_used:
        return
    numbers = ", ".join(f"{n}번" for n in result.rental_used)
    left = f" 남은 대여권 {result.rental_remaining}장" if result.rental_remaining is not None else ""
    message = f"[{result.title or webtoon['title']}] 대여권 {len(result.rental_used)}장을 사용했습니다 ({numbers}).{left}"
    if not await discord_notify.send_webhook_notification(session, settings, message):
        job_status.log_line("download", f"[{webtoon['title']}] 대여권 사용 알림을 보내지 못했습니다(웹훅 없음/전송 실패)")


async def _notify_kakao_ticket_needed(session, settings, webtoon: dict, result) -> None:
    """다음 회차가 대여권이 필요해서 더 못 받으면 디스코드로 알린다. 같은 회차는 한 번만 알리고(알린 번호를 기록),
    웹훅이 없거나 전송이 실패하면 기록하지 않아 다음 실행 때 다시 시도한다."""
    needed = result.ticket_needed
    if needed is None or webtoon.get("ticket_notified_no") == needed.number:
        return
    message = f"[{result.title or webtoon['title']}] 다음 회차 ({needed.number}번 '{needed.subtitle}')부터는 대여권 충전이 필요합니다."
    if await discord_notify.send_webhook_notification(session, settings, message):
        repository.set_kakao_ticket_notified(webtoon["title_id"], needed.number)
        job_status.log_line("download", f"[{webtoon['title']}] 대여권 필요 알림 전송 ({needed.number}번 회차)")


async def _notify_kakao_caught_up(session, settings, webtoon: dict, result) -> None:
    """연재작을 최신 회차까지 다 받아서 더 받을 회차가 없으면 디스코드로 알린다. 같은 마지막 회차는 한 번만 알린다.
    이미 따라잡은 채로 처음 확인되는 작품(이번에 받은 회차가 없고 기록도 없음)은 알리지 않고 기준만 기록한다 — 안 그러면 이 기능을 켠 첫 실행에
    구독 중인 연재작 전부에 알림이 간다. 웹훅이 없거나 전송이 실패하면 기록하지 않아 다음 실행 때 다시 시도한다."""
    last = result.caught_up_episode
    notified = webtoon.get("caught_up_notified_no")
    if last is None or notified == last.number:
        return
    if notified is None and not result.downloaded:
        repository.set_kakao_caught_up_notified(webtoon["title_id"], last.number)
        return
    message = f"[{result.title or webtoon['title']}] 더 이상 받을 회차가 없습니다. (마지막 {last.number}번 '{last.subtitle}')"
    if await discord_notify.send_webhook_notification(session, settings, message):
        repository.set_kakao_caught_up_notified(webtoon["title_id"], last.number)
        job_status.log_line("download", f"[{webtoon['title']}] 더 이상 받을 회차 없음 알림 전송 (마지막 {last.number}번)")


def _kakao_download_summary(total: int, results: list, use_rental_tickets: bool) -> str:
    """카카오페이지 자동 다운로드 한 번의 결과를 한 줄로 — 아무것도 안 받은 실행도 왜 그런지(대여권이 없는지, 기다무 충전을 기다리는지) 보이게 한다.
    results는 작품별 결과이고 예외로 중단된 작품은 None이다."""
    done = [r for r in results if r is not None]
    errors = (len(results) - len(done)) + sum(1 for r in done if r.error is not None or r.failed is not None)
    parts = [f"작품 {total}개", f"받음 {sum(len(r.downloaded) for r in done)}회차"]
    if use_rental_tickets:
        parts.append(f"대여권 {sum(len(r.rental_used) for r in done)}장 사용")
    parts.append(f"대여권이 필요해 멈춘 작품 {sum(1 for r in done if r.ticket_needed is not None)}개")
    parts.append(f"기다무 충전을 기다리는 작품 {sum(1 for r in done if r.error is None and r.failed is None and r.waiting_for_waitfree > 0)}개")
    parts.append(f"오류 {errors}개")
    return "카카오페이지 다운로드 요약 — " + " / ".join(parts)


async def _download_kakao_subscriptions(settings, failures: list[dict]) -> None:
    """구독 중인 카카오페이지 작품을 폴더 규칙(폴더 없음 → 처음부터 / 파일 여러 개 → 누락 회차 / 파일 1개 → 그 이후)
    으로 받는다(이미 받은 회차는 다시 받지 않는다). 다운로드 스케줄의 대상에 카카오페이지가 들어 있을 때만 불리고,
    로그인 쿠키가 없거나 로그인이 풀려 있으면 받지 않고(풀림은 디스코드로 알림) 건너뛴다."""
    subscribed = repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)
    if not subscribed:
        return

    async with kakao_page_download.download_lock:
        root = download_roots.kakao_root(settings)
        job_status.log_line("download", f"카카오페이지 다운로드 시작 — 구독 중인 웹툰 {len(subscribed)}개 (저장 폴더: {root})")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            client = kakao_page_download.client_from_saved_cookies(session, settings.request_timeout_seconds)
            if client is None:
                job_status.log_line("download", "카카오페이지 로그인 쿠키가 없어서 건너뜁니다(설정에서 쿠키를 저장해주세요)")
                return
            logged_in = await client.check_login()
            status = kakao_page_auth.cookie_status(kakao_page_auth.load_cookies())
            await kakao_page_auth.notify_if_needed(session, settings, logged_in, status["days_left"])
            if logged_in is False:
                job_status.log_line("download", "카카오페이지 로그인이 풀려 있어서 건너뜁니다(쿠키를 다시 붙여넣어 주세요)")
                return

            use_rental_tickets = repository.is_kakao_rental_ticket_auto_use_enabled()
            if use_rental_tickets:
                job_status.log_line("download", "대여권 자동 사용이 켜져 있습니다 — 기다무로 못 여는 회차는 작품의 보유 대여권으로 엽니다")
            results = []  # 작품별 결과(예외로 중단되면 None) — 끝에 요약 한 줄을 남긴다
            for webtoon in subscribed:
                result = await _download_kakao_title(client, settings, webtoon, root, failures, use_rental_tickets=use_rental_tickets, session=session)
                results.append(result)
                # 쉬는 시간이 긴 이어받기 중에 쿠키가 갱신될 수 있어서 작품마다 저장한다(중간에 멈춰도 갱신분이 남도록)
                kakao_page_download.persist_refreshed_cookies(client)
                if result is None:
                    continue
                # 완결이고 받을 회차를 다 받았으면 완결 확인 대상으로 기록한다(완결인데 받을 게 남았으면 아직 알리지 않는다).
                # 연재 중으로 돌아왔으면 기록을 되돌려 다음 완결 때 다시 알린다.
                if result.error is None:
                    repository.set_kakao_finished(webtoon["title_id"], result.finished and result.nothing_left)
                await _notify_kakao_ticket_needed(session, settings, webtoon, result)
                await _notify_kakao_caught_up(session, settings, webtoon, result)
                await asyncio.sleep(settings.delay_seconds)
            job_status.log_line("download", _kakao_download_summary(len(subscribed), results, use_rental_tickets))
        await _notify_kakao_newly_finished()


async def _notify_kakao_newly_finished() -> None:
    """완결이고 받을 회차를 다 받았는데 아직 알리지 않은 구독 중인 카카오 작품에 완결 확인 메시지(구독해제/알람 제외 버튼)를 보낸다.
    봇이 꺼져 있거나 전송이 실패하면 알림 기록을 남기지 않아 다음 실행 때 다시 시도한다."""
    sent = 0
    for wt in repository.list_kakao_finish_pending():
        try:
            if await discord_bot.send_completion_prompt(str(wt["title_id"]), wt["title"], platform="kakao"):
                repository.set_kakao_finish_notified(wt["title_id"])
                sent += 1
        except Exception as e:
            log.error("카카오 완결 알림 전송 중 예외 (series_id=%s): %s", wt["title_id"], e)
    if sent:
        job_status.log_line("download", f"카카오페이지 완결 확인 알림 {sent}건 전송")
