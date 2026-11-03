"""리포트 메시지 만들기 — 받은 회차/실패/새 에피소드 섹션을 디스코드 한 통의 글로 만든다(순수 함수 — 네트워크/저장소를 부르지 않는다).
"""

import logging
from datetime import datetime


from urllib.parse import quote
from zoneinfo import ZoneInfo


from app.constants import NAVER_DETAIL_URL_TEMPLATES


log = logging.getLogger(__name__)

REPORT_LIST_LIMIT = 40  # 디스코드 메시지 길이 제한 대비, 항목이 너무 많으면 일부만 나열


def build_report_message(
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
        "\n".join(success_lines[:REPORT_LIST_LIMIT]) if success_lines else "없음",
    ]
    if len(success_lines) > REPORT_LIST_LIMIT:
        parts.append(f"_외 {len(success_lines) - REPORT_LIST_LIMIT}개 생략_")

    if failed_titles:
        parts.extend([
            "",
            f"❌ 실패한 작품 ({len(failed_titles)}):",
            "\n".join(failed_titles[:REPORT_LIST_LIMIT]),
        ])
        if len(failed_titles) > REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(failed_titles) - REPORT_LIST_LIMIT}개 생략_")

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
            "\n".join(new_lines[:REPORT_LIST_LIMIT]),
        ])
        if len(new_lines) > REPORT_LIST_LIMIT:
            parts.append(f"_외 {len(new_lines) - REPORT_LIST_LIMIT}개 생략_")

    return "\n".join(parts)
