"""리포트 메시지 만들기 — 받은 회차/실패/새 에피소드 섹션을 디스코드 한 통의 글로 만든다(순수 함수 — 네트워크/저장소를 부르지 않는다).
"""

import logging
from datetime import datetime


from urllib.parse import quote
from zoneinfo import ZoneInfo


from app.constants import NAVER_DETAIL_URL_TEMPLATES


log = logging.getLogger(__name__)

REPORT_LIST_LIMIT = 40  # 디스코드 메시지 길이 제한 대비, 항목이 너무 많으면 일부만 나열
NAVER_TITLE_PAGE_URL = "https://comic.naver.com/webtoon/list?titleId="  # 신규 웹툰의 "정보 보기"
KAKAO_TITLE_PAGE_URL = "https://page.kakao.com/content/"


def _platform_label(platform: str) -> str:
    return "카카오" if platform == "kakao" else "네이버"


def _platform_sort_key(key: tuple[str, str]) -> tuple[bool, str]:
    """네이버가 먼저, 그 안에서 가나다 순."""
    return (key[0] != "naver", key[1])


def _limited(lines: list[str]) -> list[str]:
    """한도(REPORT_LIST_LIMIT)까지만 나열하고, 넘으면 "_외 N개 생략_"을 덧붙인다(디스코드 메시지 길이 제한 대비)."""
    block = ["\n".join(lines[:REPORT_LIST_LIMIT])]
    if len(lines) > REPORT_LIST_LIMIT:
        block.append(f"_외 {len(lines) - REPORT_LIST_LIMIT}개 생략_")
    return block


def _success_lines(success_rows: list[dict], reader_urls: dict[str, str], tag_platforms: bool) -> list[str]:
    """받은 작품 한 줄씩. 같은 작품의 여러 회차는 한 줄로 묶는다. 카카오는 받은 회차 수/범위와(뷰어에 없으면) 카카오페이지 링크를 보여준다 — reader_urls의 카카오 키는 "kakao:제목"."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in success_rows:
        groups.setdefault((row.get("platform") or "naver", row["title_name"]), []).append(row)
    lines = []
    for platform, title in sorted(groups, key=_platform_sort_key):
        prefix = f"[{_platform_label(platform)}] " if tag_platforms else ""
        if platform == "kakao":
            group = groups[(platform, title)]
            numbers = sorted({row["episode_no"] for row in group})
            span = f"{numbers[0]}번" if len(numbers) == 1 else f"{numbers[0]}~{numbers[-1]}번"
            url = reader_urls.get(f"kakao:{title}") or f"https://page.kakao.com/content/{group[0]['title_id']}"
            lines.append(f"• {prefix}{title} · {len(numbers)}개 회차({span}) [바로가기](<{url}>)")
        else:
            url = reader_urls.get(title)
            lines.append(f"• {prefix}{title} [바로가기](<{url}>)" if url else f"• {prefix}{title}")
    return lines


def _failed_titles(failed_rows: list[dict], tag_platforms: bool) -> list[str]:
    keys = sorted({(row.get("platform") or "naver", row["title_name"]) for row in failed_rows}, key=_platform_sort_key)
    return [f"[{_platform_label(platform)}] {title}" if tag_platforms else title for platform, title in keys]


def _new_episode_lines(
    unregistered_new_episodes: list[tuple[str, str, int]] | None, kakao_new_episodes: list[tuple[int, str, str]] | None, app_public_base_url: str,
) -> list[str]:
    """등록 안 한 작품의 새 에피소드 한 줄씩(네이버, 카카오 순). 앱 주소가 있으면 "목록 제외" 링크를 붙인다 — 여기 나오는 건 전부 미구독 작품이다."""
    lines = []
    for title_id, title, episode_no in unregistered_new_episodes or []:
        read_url = f"{NAVER_DETAIL_URL_TEMPLATES['webtoon']}?titleId={title_id}&no={episode_no}"
        line = f"• [네이버] {title} [바로가기](<{read_url}>)"
        if app_public_base_url:
            line += f" · [목록 제외](<{app_public_base_url}/api/webtoons/{title_id}/exclude-confirm?title={quote(title)}>)"
        lines.append(line)
    for title_id, title, url in kakao_new_episodes or []:
        line = f"• [카카오] {title} [바로가기](<{url}>)"
        if app_public_base_url:
            line += f" · [목록 제외](<{app_public_base_url}/api/kakao-webtoons/{title_id}/exclude-confirm?title={quote(title)}>)"
        lines.append(line)
    return lines


def _new_title_lines(
    new_titles: list[tuple[str, str]] | None, kakao_new_titles: list[tuple[int, str]] | None, app_public_base_url: str,
) -> list[str]:
    """신규 웹툰 한 줄씩(네이버, 카카오 순) — 웹툰명 옆에 "정보 보기"(작품 페이지)와 "목록 제외" 링크(앱 주소가 있을 때). 디스코드 웹훅은 버튼을 못 만들어서 링크로 보여 준다."""
    lines = []
    for title_id, title in new_titles or []:
        line = f"• [네이버] {title} · [정보 보기](<{NAVER_TITLE_PAGE_URL}{title_id}>)"
        if app_public_base_url:
            line += f" · [목록 제외](<{app_public_base_url}/api/webtoons/{title_id}/exclude-confirm?title={quote(title)}>)"
        lines.append(line)
    for title_id, title in kakao_new_titles or []:
        line = f"• [카카오] {title} · [정보 보기](<{KAKAO_TITLE_PAGE_URL}{title_id}>)"
        if app_public_base_url:
            line += f" · [목록 제외](<{app_public_base_url}/api/kakao-webtoons/{title_id}/exclude-confirm?title={quote(title)}>)"
        lines.append(line)
    return lines


def _list_alert_sections(
    new_titles, kakao_new_titles, unregistered_new_episodes, kakao_new_episodes, app_public_base_url: str,
) -> list[str]:
    """웹툰 전체목록 알림의 섹션들(신규 웹툰 → 새 에피소드) — 비어 있는 섹션은 뺀다. 다운로드 리포트에 얹을 때와 단독 알림이 같이 쓴다."""
    parts: list[str] = []
    title_lines = _new_title_lines(new_titles, kakao_new_titles, app_public_base_url)
    if title_lines:
        parts.extend(["", f"🆕 웹툰 전체목록 중 신규 웹툰 ({len(title_lines)}):", *_limited(title_lines)])
    episode_lines = _new_episode_lines(unregistered_new_episodes, kakao_new_episodes, app_public_base_url)
    if episode_lines:
        parts.extend(["", f"🆕 웹툰 전체목록 중 새 에피소드 ({len(episode_lines)}):", *_limited(episode_lines)])
    return parts


def build_report_message(
    success_rows: list[dict], failed_rows: list[dict], reader_urls: dict[str, str],
    unregistered_new_episodes: list[tuple[str, str, int]] | None = None,
    app_public_base_url: str = "",
    kakao_new_episodes: list[tuple[int, str, str]] | None = None,
    new_titles: list[tuple[str, str]] | None = None,
    kakao_new_titles: list[tuple[int, str]] | None = None,
) -> str:
    """예전 hermes webtoon_checker.py의 메시지 구조(다운로드됨/실패)를 그대로 따른다: 제목 줄 → 받은 작품 → (있으면) 실패 → (있으면) 신규 웹툰 → (있으면) 새 에피소드.
    모든 링크는 <...>로 감싸서 디스코드가 미리보기(임베드)를 안 만들게 한다 — 링크가 여러 개 나열될 때 임베드가 줄줄이 생겨서 메시지가 너무 길어지는 문제가 있었다.
    카카오페이지 이력이 섞여 있으면 어느 플랫폼인지 앞에 붙인다(네이버만 있을 땐 예전 형식 그대로)."""
    today_label = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    tag_platforms = any(row.get("platform") == "kakao" for row in success_rows + failed_rows)

    success_lines = _success_lines(success_rows, reader_urls, tag_platforms)
    parts = [f"📅 웹툰 다운로드 리포트 ({today_label})", "", f"📁 받은 작품 ({len(success_lines)}):"]
    parts.extend(_limited(success_lines) if success_lines else ["없음"])

    failed_titles = _failed_titles(failed_rows, tag_platforms)
    if failed_titles:
        parts.extend(["", f"❌ 실패한 작품 ({len(failed_titles)}):", *_limited(failed_titles)])

    parts.extend(_list_alert_sections(new_titles, kakao_new_titles, unregistered_new_episodes, kakao_new_episodes, app_public_base_url))
    return "\n".join(parts)


def build_list_alert_message(
    new_titles: list[tuple[str, str]], kakao_new_titles: list[tuple[int, str]],
    unregistered_new_episodes: list[tuple[str, str, int]], kakao_new_episodes: list[tuple[int, str, str]], app_public_base_url: str,
) -> str:
    """웹툰 전체목록 알림만 따로 보낼 때의 메시지(다운로드 이력 없이 신규 웹툰 + 새 에피소드). 보낼 섹션이 하나도 없으면 빈 문자열 — 호출하는 쪽이 보내지 않는다."""
    sections = _list_alert_sections(new_titles, kakao_new_titles, unregistered_new_episodes, kakao_new_episodes, app_public_base_url)
    if not sections:
        return ""
    today_label = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    return "\n".join([f"🔔 웹툰 전체목록 알림 ({today_label})", *sections])
