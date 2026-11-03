"""리포트 메시지 만들기(report_message.build_report_message) — 순수 함수라서 입력별로 글을 정확히 고정한다.
구성: 제목 줄(오늘 날짜) → 받은 작품 → (있으면) 실패한 작품 → (있으면) 새 에피소드. 카카오가 섞이면 플랫폼 꼬리표, 모든 링크는 <...>로 감싸 미리보기를 막고,
항목이 REPORT_LIST_LIMIT을 넘으면 앞부분만 나열하고 "_외 N개 생략_"을 붙인다.
"""
from urllib.parse import quote

from app.scheduler import report_message as rm

class FixedDate:
    @classmethod
    def now(cls, tz=None):
        import datetime as real
        return real.datetime(2026, 11, 3, 12, 0, tzinfo=tz)
rm.datetime = FixedDate
build = rm.build_report_message
HEADER = "📅 웹툰 다운로드 리포트 (2026-11-03)"
ok_row = lambda title, no=1, platform=None, title_id="1": {"title_name": title, "episode_no": no, "platform": platform, "title_id": title_id}
fail_row = lambda title, platform=None: {"title_name": title, "platform": platform}

# Scenario 1: 네이버만 — 예전 형식 그대로(꼬리표 없음). 뷰어 링크가 있는 작품만 "바로가기", 같은 작품의 여러 회차는 한 줄, 가나다 순
msg = build([ok_row("나작품", 1), ok_row("가작품", 2), ok_row("가작품", 3)], [], {"가작품": "https://viewer/가"})
assert msg == "\n".join([HEADER, "", "📁 받은 작품 (2):", "• 가작품 [바로가기](<https://viewer/가>)\n• 나작품"]), msg

# Scenario 2: 받은 게 없으면 "없음", 실패가 있으면 실패 섹션(중복 이름은 하나로, 가나다 순)
assert build([], [], {}) == "\n".join([HEADER, "", "📁 받은 작품 (0):", "없음"])
msg = build([], [fail_row("다"), fail_row("가"), fail_row("가")], {})
assert msg == "\n".join([HEADER, "", "📁 받은 작품 (0):", "없음", "", "❌ 실패한 작품 (2):", "가\n다"]), msg

# Scenario 3: 카카오가 섞이면 플랫폼 꼬리표 — 이름순이 아니라 네이버가 먼저(그래서 카카오 작품 이름이 가나다 순으로 앞서도 뒤에 나온다), 그 안에서 가나다 순. 카카오는 받은 회차 수/범위(한 개면 "N번", 여럿이면 "처음~끝번")와 뷰어 링크(없으면 카카오페이지 작품 링크)
rows = [ok_row("가카오B", 5, "kakao", "777"), ok_row("가카오B", 3, "kakao", "777"), ok_row("가카오B", 4, "kakao", "777"), ok_row("가카오A", 9, "kakao", "888"), ok_row("나네이버", 1, None, "1")]
msg = build(rows, [fail_row("가카오F", "kakao"), fail_row("나네이버F")], {"kakao:가카오A": "https://viewer/ka"})
assert msg == "\n".join([HEADER, "", "📁 받은 작품 (3):",
    "• [네이버] 나네이버\n• [카카오] 가카오A · 1개 회차(9번) [바로가기](<https://viewer/ka>)\n• [카카오] 가카오B · 3개 회차(3~5번) [바로가기](<https://page.kakao.com/content/777>)",
    "", "❌ 실패한 작품 (2):", "[네이버] 나네이버F\n[카카오] 가카오F"]), msg

# Scenario 4: 새 에피소드(등록 안 한 작품) — 네이버는 회차 링크, 카카오는 받은 링크. 앱 주소가 있으면 "목록 제외" 링크(제목은 URL 인코딩)를 붙이고 없으면 안 붙인다. 개수는 합계
naver_new = [("11", "새작 A", 7)]; kakao_new = [(22, "새작 B", "https://page.kakao.com/content/22")]
msg = build([ok_row("가")], [], {}, naver_new, "", kakao_new)
assert msg.endswith("\n".join(["", "🆕 웹툰 전체목록 중 새 에피소드 (2):", f"• [네이버] 새작 A [바로가기](<{rm.NAVER_DETAIL_URL_TEMPLATES['webtoon']}?titleId=11&no=7>)\n• [카카오] 새작 B [바로가기](<https://page.kakao.com/content/22>)"])), msg
msg = build([ok_row("가")], [], {}, naver_new, "https://app.example", kakao_new)
assert f"• [네이버] 새작 A [바로가기](<{rm.NAVER_DETAIL_URL_TEMPLATES['webtoon']}?titleId=11&no=7>) · [목록 제외](<https://app.example/api/webtoons/11/exclude-confirm?title={quote('새작 A')}>)" in msg
assert f"• [카카오] 새작 B [바로가기](<https://page.kakao.com/content/22>) · [목록 제외](<https://app.example/api/kakao-webtoons/22/exclude-confirm?title={quote('새작 B')}>)" in msg

# Scenario 5: 한도(REPORT_LIST_LIMIT)를 넘으면 앞에서부터 한도까지만 나열하고 "_외 N개 생략_" — 받은 작품/실패/새 에피소드 각각, 한도 이하면 생략 문구 없음
limit = rm.REPORT_LIST_LIMIT
many_ok = [ok_row(f"작품{i:03d}") for i in range(limit + 5)]
msg = build(many_ok, [fail_row(f"실패{i:03d}") for i in range(limit + 2)], {}, [(str(i), f"새{i:03d}", 1) for i in range(limit + 3)])
lines = msg.split("\n")
assert f"📁 받은 작품 ({limit + 5}):" in lines and "_외 5개 생략_" in lines and f"❌ 실패한 작품 ({limit + 2}):" in lines and "_외 2개 생략_" in lines and "_외 3개 생략_" in lines
assert lines.count("• 작품000") == 1 and not any("작품" + f"{limit:03d}" in l for l in lines), "한도 뒤는 나열하지 않는다"
exact = build([ok_row(f"작품{i:03d}") for i in range(limit)], [], {}); assert "생략" not in exact and exact.count("• ") == limit
print("ALL OK")
