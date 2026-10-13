"""리포트 작업 — 신규 회차/완결 요약을 모아 디스코드로 보낸다.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from app import daily_plus, discord_notify, job_status, kakao_api, kakao_page_auth, naver_api, report_seen, repository, webtoon_server_client
from app.config import get_settings
from app.constants import NAVER_DETAIL_URL_TEMPLATES
from app.cookie_loader import get_adult_cookies
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao

log = logging.getLogger(__name__)

_report_job_lock = asyncio.Lock()


_SETTING_KEY_REPORT_LAST_SENT_AT = "report_last_sent_at"


_SETTING_KEY_WEBTOON_SERVER_URL = "webtoon_server_url"


_REPORT_LIST_LIMIT = 40  # 디스코드 메시지 길이 제한 대비, 항목이 너무 많으면 일부만 나열


_UNREGISTERED_NEW_EPISODE_LIMIT = 20  # 미등록 신규 에피소드는 네이버에 회차번호를 하나씩 더 물어봐야 해서, 너무 많이 걸면 부담이라 따로 더 작게 제한


# 카카오는 "웹툰 전체목록"에 있는(=요일 7개, 제외 안 한) 것 전부가 후보 대상이라
# 네이버의 "완전 미등록"보다 모수가 훨씬 크다 — UP 표시된 게 20개를 넘는 날은 실제로
# 흔해서, 위 한도를 그대로 쓰면 뒤로 밀린 작품(카탈로그에서 나중에 나온 것)의 새
# 에피소드가 후보에도 못 들고 조용히 빠지는 문제가 있었다(실제로 확인됨: "바퀴벌레
# 잔혹사"가 UP인데 리포트엔 안 나옴). 조회 자체(뷰어/카카오 최신 회차 확인)는 이미
# 동시 실행 수 제한(artist_scan_concurrency)이 있어서 이 한도를 넉넉히 키워도
# 부담이 크지 않고, 실제 디스코드 메시지에 보여줄 개수는 아래 _build_report_message
# 에서 _REPORT_LIST_LIMIT로 별도로 줄인다(둘을 분리해야 "후보에서 누락"과 "메시지에서
# 생략 표시하며 자름"이 다른 문제라는 게 명확해진다).
_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT = 150


def _log_unchecked_adult(platform_label: str, titles: list[str], how_to_fix: str) -> None:
    """성인 작품의 새 회차를 쿠키 문제로 확인하지 못했을 때 조용히 넘기지 않고 리포트 실행 로그에 남긴다(리포트 메시지에는 넣지 않는다)."""
    if not titles:
        return
    shown = ", ".join(titles[:5]) + (" 등" if len(titles) > 5 else "")
    message = f"{platform_label} 성인 작품 {len(titles)}개는 새 에피소드를 확인하지 못했습니다({shown}) — {how_to_fix}"
    log.warning(message)
    job_status.log_line("report", message)


async def _collect_unregistered_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[str, str, int]]:
    """네이버 "웹툰 전체목록"에 보이는 **미구독** 작품 중 새 에피소드(UP 표시)가 있는 것만 골라서
    (title_id, title_name, 최신 회차 번호)로 반환한다(카카오 _collect_kakao_new_episodes와 같은 규칙).
    미구독 = 기록이 없거나, 구독한 적이 있지만 "목록으로" 되돌려 "목록" 상태인 작품. 구독 중인 작품은
    다운로드로 알리고, 구독해제/제외됨은 "안 챙겨본다"는 뜻이라 알리지 않는다. 구독을 안 해서 놓치고
    있었을 수도 있는 신작을 리포트에서 바로 발견하게 해 주는 용도라, 없는 게 정상인 경우가 대부분이고
    실패해도 리포트 자체는 계속 보내야 한다."""
    try:
        items = await naver_api.fetch_full_webtoon_list(session, settings.request_timeout_seconds)
    except Exception as e:
        log.error("미등록 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    def _not_subscribed_in_list(title_id: str) -> bool:
        record = repository.get(title_id)
        return record is None or record.status == repository.STATUS_UNREGISTERED

    hidden = await daily_plus.current_ids(settings.request_timeout_seconds, session)  # 매일+ 작품은 화면에서 숨기므로 알리지도 않는다(목록을 모르면 거르지 않음)
    candidates = [item for item in items if item.has_update and item.title_id not in hidden and _not_subscribed_in_list(item.title_id)]
    candidates = candidates[:_UNREGISTERED_NEW_EPISODE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 성인 작품은 성인 인증 쿠키가 없으면 회차 목록이 비어서 와서 최신 회차를 알 수 없다 — 다운로드와 같은 쿠키로 조회한다
    adult_cookies = get_adult_cookies(settings.cookie_file_path)

    async def _fetch_one(item):
        async with semaphore:
            try:
                episode_no = await naver_api.fetch_latest_episode_no(
                    session, item.title_id, settings.request_timeout_seconds, cookies=adult_cookies if item.is_adult else None,
                )
            except Exception as e:
                log.error("미등록 작품(titleId=%s) 최신 회차 조회 실패(건너뜀): %s", item.title_id, e)
                episode_no = None
            await asyncio.sleep(settings.delay_seconds)
            return item, episode_no

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "네이버", [item.title_name for item, episode_no in results if episode_no is None and item.is_adult],
        "성인 인증 쿠키 파일(COOKIE_DIR_HOST_PATH)이 없거나 만료됐을 수 있습니다",
    )
    return [
        (item.title_id, item.title_name, episode_no)
        for item, episode_no in results
        if episode_no is not None
    ]


async def _collect_kakao_new_episodes(
    session: aiohttp.ClientSession, settings
) -> list[tuple[int, str, str]]:
    """네이버의 _collect_unregistered_new_episodes와 같은 취지로, 카카오페이지 "웹툰 전체목록"에 있는 **미구독** 웹툰 중 새 회차(UP
    표시)가 있는 것만 골라 (title_id, title_name, 카카오페이지 바로가기 URL)로 반환한다. 구독을 안 해서 놓치고 있던 신작을 리포트에서
    발견하게 해 주는 용도다.

    구독 중인 작품은 이 앱이 직접 받아서 "받은 작품"에 나오므로 여기서는 뺀다(예전엔 카카오 "구독"이 웹툰 뷰어에 있다는 표시일 뿐이라
    구독 중인 작품도 알려 줬지만, 지금은 중복 알림이다). 구독해제/제외됨도 "더 이상 안 챙겨본다"는 뜻이라 뺀다. 목록에 그대로 보이는
    "목록" 상태와 아직 아무 기록이 없는 작품만 후보로 남는다. 구독하지 않은 작품은 웹툰 뷰어에 없으니 링크는 항상 카카오페이지 것이다."""
    try:
        # 리포트는 화면 캐시(10분)가 아니라 그 시점의 최신 목록으로 만든다
        items = await kakao_api.fetch_weekday_catalog(session, settings.request_timeout_seconds, use_cache=False)
    except Exception as e:
        log.error("카카오 신규 에피소드 확인 중 목록 조회 실패(무시하고 계속): %s", e)
        return []

    skipped_ids = (
        repository.get_kakao_excluded_title_ids()
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_UNSUBSCRIBED)}
        | {wt["title_id"] for wt in repository.list_kakao_webtoons_by_status(repository.STATUS_ACTIVE)}
    )
    candidates = [item for item in items if item["has_update"] and item["title_id"] not in skipped_ids]
    candidates = candidates[:_KAKAO_NEW_EPISODE_CANDIDATE_LIMIT]
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(settings.artist_scan_concurrency)
    # 19세 작품은 로그인한 계정으로 조회한다(네이버 성인 작품과 같은 규칙) — 저장된 카카오페이지 로그인 쿠키가 없으면 로그인 없이 시도
    saved = kakao_page_auth.load_cookies()
    login_cookies = kakao_page_auth.cookie_map(saved) if saved else None

    async def _fetch_one(item):
        async with semaphore:
            try:
                url = await kakao_api.fetch_latest_episode_url(
                    session, item["title_id"], settings.request_timeout_seconds, cookies=login_cookies if item["is_adult"] else None,
                )
            except Exception as e:
                log.error("카카오 작품(title_id=%s) 최신 회차 조회 실패(건너뜀): %s", item["title_id"], e)
                url = None
            await asyncio.sleep(settings.delay_seconds)
            return item, url

    results = await asyncio.gather(*[_fetch_one(item) for item in candidates])
    _log_unchecked_adult(
        "카카오", [item["title_name"] for item, url in results if url is None and item["is_adult"]],
        "카카오페이지 로그인 쿠키가 없거나 만료됐을 수 있습니다(설정 > 카카오웹툰 관리)",
    )
    final = [(item["title_id"], item["title_name"], url) for item, url in results if url is not None]
    if len(final) < len(candidates):
        # 후보였는데 최종 리포트엔 안 실린 개수 — 조회 실패(HTTP 403 등)로 조용히
        # 빠진 게 몇 개인지 여기서 한눈에 보이게 남겨둔다. 원인 자체는 위
        # fetch_latest_episode_url/조회 실패 경고 로그에 있지만, 매번 그걸 하나하나
        # 찾아보지 않아도 "얼마나 빠졌는지"는 바로 알 수 있어야 한다.
        log.warning(
            "카카오 새 에피소드 후보 %d개 중 %d개만 최종 리포트에 포함됨(나머지는 회차 조회 실패)",
            len(candidates), len(final),
        )
    return final


def _build_report_message(
    success_rows: list[dict], failed_rows: list[dict], reader_urls: dict[str, str],
    unregistered_new_episodes: list[tuple[str, str, int]] | None = None,
    app_public_base_url: str = "",
    kakao_new_episodes: list[tuple[int, str, str]] | None = None,
) -> str:
    """예전 hermes webtoon_checker.py의 메시지 구조(다운로드됨/실패)를 그대로 따른다.
    모든 링크는 <...>로 감싸서 디스코드가 미리보기(임베드)를 안 만들게 한다 — 링크가
    여러 개 나열될 때 임베드가 줄줄이 생겨서 메시지가 너무 길어지는 문제가 있었다."""
    today_label = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    # 카카오페이지 이력이 섞여 있으면 어느 플랫폼인지 앞에 붙이고, 카카오는 받은 회차 수/범위와(뷰어에 없으면) 카카오페이지 링크를 보여준다.
    # 네이버만 있을 땐 예전 형식 그대로다. reader_urls의 카카오 키는 "kakao:제목"이다.
    tag_platforms = any(r.get("platform") == "kakao" for r in success_rows + failed_rows)
    success_groups: dict[tuple[str, str], list[dict]] = {}
    for r in success_rows:
        success_groups.setdefault((r.get("platform") or "naver", r["title_name"]), []).append(r)
    success_titles = sorted(success_groups, key=lambda key: (key[0] != "naver", key[1]))
    success_lines = []
    for platform, title in success_titles:
        prefix = f"[{'카카오' if platform == 'kakao' else '네이버'}] " if tag_platforms else ""
        if platform == "kakao":
            group = success_groups[(platform, title)]
            numbers = sorted({r["episode_no"] for r in group})
            span = f"{numbers[0]}번" if len(numbers) == 1 else f"{numbers[0]}~{numbers[-1]}번"
            url = reader_urls.get(f"kakao:{title}") or f"https://page.kakao.com/content/{group[0]['title_id']}"
            success_lines.append(f"• {prefix}{title} · {len(numbers)}개 회차({span}) [바로가기](<{url}>)")
        else:
            url = reader_urls.get(title)
            success_lines.append(f"• {prefix}{title} [바로가기](<{url}>)" if url else f"• {prefix}{title}")

    failed_keys = sorted({(r.get("platform") or "naver", r["title_name"]) for r in failed_rows}, key=lambda key: (key[0] != "naver", key[1]))
    failed_titles = [f"[{'카카오' if p == 'kakao' else '네이버'}] {t}" if tag_platforms else t for p, t in failed_keys]

    parts = [
        f"📅 웹툰 다운로드 리포트 ({today_label})",
        "",
        f"📁 받은 작품 ({len(success_titles)}):",
        "\n".join(success_lines[:_REPORT_LIST_LIMIT]) if success_lines else "없음",
    ]
    if len(success_lines) > _REPORT_LIST_LIMIT:
        parts.append(f"_외 {len(success_lines) - _REPORT_LIST_LIMIT}개 생략_")

    if failed_titles:
        parts.extend([
            "",
            f"❌ 실패한 작품 ({len(failed_titles)}):",
            "\n".join(failed_titles[:_REPORT_LIST_LIMIT]),
        ])
        if len(failed_titles) > _REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(failed_titles) - _REPORT_LIST_LIMIT}개 생략_")

    new_lines = []
    for title_id, title, episode_no in unregistered_new_episodes or []:
        read_url = f"{NAVER_DETAIL_URL_TEMPLATES['webtoon']}?titleId={title_id}&no={episode_no}"
        line = f"• [네이버] {title} [바로가기](<{read_url}>)"
        if app_public_base_url:
            exclude_url = f"{app_public_base_url}/api/webtoons/{title_id}/exclude-confirm?title={quote(title)}"
            line += f" · [목록 제외](<{exclude_url}>)"
        new_lines.append(line)
    for title_id, title, url in kakao_new_episodes or []:
        line = f"• [카카오] {title} [바로가기](<{url}>)"
        if app_public_base_url:  # 여기 나오는 건 전부 미구독 작품이라 "목록 제외" 링크를 붙인다
            exclude_url = f"{app_public_base_url}/api/kakao-webtoons/{title_id}/exclude-confirm?title={quote(title)}"
            line += f" · [목록 제외](<{exclude_url}>)"
        new_lines.append(line)
    if new_lines:
        parts.extend([
            "",
            f"🆕 웹툰 전체목록 중 새 에피소드 ({len(new_lines)}):",
            "\n".join(new_lines[:_REPORT_LIST_LIMIT]),
        ])
        if len(new_lines) > _REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(new_lines) - _REPORT_LIST_LIMIT}개 생략_")

    return "\n".join(parts)


async def run_report_job(force_test: bool = False) -> None:
    if _report_job_lock.locked():
        job_status.log_line("report", "이미 실행 중이라 건너뜁니다 (중복 실행 방지)")
        return
    async with _report_job_lock:
        await _run_report_job_impl(force_test=force_test)


_KST = ZoneInfo("Asia/Seoul")


def _kst_day_range_utc(day) -> tuple[str, str]:
    """한국시간 하루(day)의 [시작, 끝)을 UTC ISO 문자열로."""
    start_kst = datetime.combine(day, datetime.min.time(), tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc).isoformat(), end_kst.astimezone(timezone.utc).isoformat()


def _select_report_rows(since: str | None, force_test: bool) -> tuple[list[dict], bool]:
    """리포트에 담을 다운로드 이력과, 어제 기록으로 대체했는지(테스트 발송에서만).

    일반 발송은 "지난 발송 이후" 이력 전부다. 테스트 발송은 그 누적 로직을 아예 안 쓴다 — 오늘(한국시간) 다운로드한 것만 보내고,
    오늘 게 없으면 어제 것만 보낸다. 전체 이력이 몰려서 나오는 걸 막기 위해 날짜 하루 단위로 딱 끊는다."""
    if not force_test:
        return (repository.list_episode_history_since(since) if since else []), False
    today = datetime.now(_KST).date()
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today))
    if rows:
        return rows, False
    rows = repository.list_episode_history_between(*_kst_day_range_utc(today - timedelta(days=1)))
    return rows, bool(rows)


async def _collect_new_episode_sections(session: aiohttp.ClientSession, settings, *, force_test: bool) -> tuple[list, list]:
    """등록 안 한 작품의 새 에피소드(네이버, 카카오 순). 다운로드 기록과 무관하게(rows가 비어 있어도) 항상 확인한다 — 구독을 안 해서
    기록 자체가 없는 작품을 발견하는 게 이 섹션의 목적이라, "받은 게 없으니 리포트도 없음"에 묻히면 안 된다. 일반 발송에서는 이미
    같은 회차로 알린 작품을 뺀다(UP 표시가 남아 있는 동안 리포트마다 반복되지 않게) — 테스트 발송은 전부 보여준다."""
    enabled = repository.get_setting("report_unregistered_new_episodes_enabled") != "0"
    kakao_enabled = repository.get_setting("kakao_webtoons_enabled") == "1"
    naver_items = await _collect_unregistered_new_episodes(session, settings) if enabled else []
    kakao_items = await _collect_kakao_new_episodes(session, settings) if enabled and kakao_enabled else []
    if not force_test:
        naver_items, kakao_items = report_seen.filter_unseen(naver_items, kakao_items)
    return naver_items, kakao_items


async def _resolve_reader_urls(session: aiohttp.ClientSession, settings, success_rows: list[dict]) -> dict[str, str]:
    """받은 작품의 웹툰 뷰어 바로가기 링크(뷰어 서버가 설정돼 있을 때만). 키: 네이버는 제목, 카카오는 "kakao:제목".
    웹툰서버는 실제 디스크 폴더명 기준으로 매칭하는데, 폴더를 만들 때는 ':' 같은 금지문자를 치환해서 저장한다 — 네이버는 전각 문자로
    (예: "제목 : 부제" → "제목 ： 부제"), 카카오는 밑줄 규칙으로. 원본 제목을 그대로 조회하면 문자가 안 맞아 실패한 사례가 있어서
    폴더명 생성과 같은 치환을 거쳐 조회한다."""
    webtoon_server_url = repository.get_setting(_SETTING_KEY_WEBTOON_SERVER_URL) or ""
    urls: dict[str, str] = {}
    if not webtoon_server_url:
        return urls
    for platform, folder_name, key_prefix in (("naver", remove_forbidden_str, ""), ("kakao", remove_forbidden_str_kakao, "kakao:")):
        for title in sorted({r["title_name"] for r in success_rows if (r.get("platform") or "naver") == platform}):
            url = await webtoon_server_client.fetch_reader_url(session, webtoon_server_url, folder_name(title), settings.request_timeout_seconds)
            if url:
                urls[f"{key_prefix}{title}"] = url
    return urls


async def _run_report_job_impl(force_test: bool = False) -> None:
    settings = get_settings()
    job_status.start("report")

    since = repository.get_setting(_SETTING_KEY_REPORT_LAST_SENT_AT)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not since and not force_test:
        # 최초 실행이면 지금까지 쌓인 이력을 전부 몰아 보내는 대신, 지금 시점부터 집계를 시작한다 — 첫 리포트에 예전 이력이 전부
        # 딸려오는 걸 방지. 수동 테스트(force_test)일 때는 이 규칙을 건너뛴다 — 사용자가 실제 발송 형태를 확인해보고 싶은 것이므로.
        repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", "최초 실행 — 이번 시점부터 집계 시작 (발송 없음)")
        job_status.finish("report", success=True)
        return

    rows, used_fallback = _select_report_rows(since, force_test)
    if not force_test:
        rows = report_seen.filter_unseen_rows(rows)  # 이미 보고한 회차는 뺀다(같은 회차는 한 번만) — 테스트 발송은 전부 보여준다
    success_rows = [r for r in rows if r["status"] == "success"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    app_public_base_url = repository.get_setting("app_public_base_url") or ""

    try:
        async with aiohttp.ClientSession() as session:
            unregistered_new_episodes, kakao_new_episodes = await _collect_new_episode_sections(session, settings, force_test=force_test)
            if not rows and not unregistered_new_episodes and not kakao_new_episodes:
                job_status.log_line("report", "발송할 내용 없음 (다운로드 기록도, 새 에피소드도 없음)")
                if not force_test:
                    repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
                job_status.finish("report", success=True)
                return

            reader_urls = await _resolve_reader_urls(session, settings, success_rows)
            message = _build_report_message(
                success_rows, failed_rows, reader_urls, unregistered_new_episodes, app_public_base_url, kakao_new_episodes
            )
            if used_fallback:
                message = "🧪 **[테스트 발송 — 오늘 기록 없어 어제 기록으로 대체됨]**\n" + message
            await discord_notify.send_webhook_notification(session, settings, message)
            if not force_test:
                # 전송이 성공한 뒤에만 기록(실패하면 다음에 다시 시도)
                report_seen.remember(unregistered_new_episodes, kakao_new_episodes)
                report_seen.remember_rows(rows)

        if not force_test:
            repository.set_setting(_SETTING_KEY_REPORT_LAST_SENT_AT, now_iso)
        job_status.log_line("report", f"리포트 발송 완료 (성공 {len(success_rows)}건, 실패 {len(failed_rows)}건)")
        job_status.finish("report", success=True)
    except Exception as e:
        log.error("리포트 발송 중 예외: %s", e)
        job_status.log_line("report", f"오류: {e}")
        job_status.finish("report", success=False)
