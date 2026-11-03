"""디스코드 리포트의 "목록 제외" 링크가 여는 확인 페이지 — 모바일에서 잘 보이고 누르기 쉬워야 한다.

예전 문제: viewport 메타가 없어서 모바일 브라우저가 페이지를 데스크톱 폭(약 980px)으로 그린 뒤 줄여서 보여 줘서 글자와 버튼이 아주 작았다.
기준: ① viewport 메타 ② 글자는 본문 16px 이상, 제목 20px 이상 ③ 버튼은 화면 폭을 꽉 채우고 높이 48px 이상(손가락으로 누르기 쉬운 크기) ④ 어두운 화면 설정에서도 보임
GET은 아무것도 바꾸지 않고(디스코드 미리보기가 자동으로 열어 보기 때문), 실제 제외는 이 페이지의 버튼이 따로 POST한다 — 이 동작은 그대로다.
"""
import asyncio
import re

import httpx

from app import db, repository
db.get_connection()
import app.main as m


def css_rule(page: str, selector: str) -> str:
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", page, re.M)
    assert match, f"CSS 규칙 없음: {selector}"
    return match.group(1)


def px(rule: str, prop: str) -> float:
    match = re.search(r"(?:^|;|\s)" + prop + r":\s*([\d.]+)px", rule)
    assert match, f"{prop} 없음: {rule}"
    return float(match.group(1))


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        naver = (await c.get("/api/webtoons/88800001/exclude-confirm", params={"title": "쌍갑포차: <b>1부</b>", "thumbnail_url": "http://t/1.jpg"}))
        kakao = (await c.get("/api/kakao-webtoons/88800002/exclude-confirm", params={"title": "도굴왕"}))
        assert naver.status_code == 200 and kakao.status_code == 200 and "text/html" in naver.headers["content-type"]
        page = naver.text

        # Scenario 1: 모바일 — viewport 메타가 있고 글자/버튼이 충분히 크다
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page, "viewport 메타가 없으면 모바일이 데스크톱 폭으로 그려서 작아 보인다"
        assert px(css_rule(page, "h1"), "font-size") >= 20 and px(css_rule(page, ".question"), "font-size") >= 16 and px(css_rule(page, ".note"), "font-size") >= 14
        btn = css_rule(page, ".btn")
        assert px(btn, "min-height") >= 48 and px(btn, "font-size") >= 17 and "width: 100%" in btn and "display: block" in btn, btn           # 화면 폭을 꽉 채우는 큰 버튼
        assert "box-sizing: border-box" in btn                                                                                         # 폭 100%가 패딩 때문에 화면 밖으로 넘치지 않게
        assert "max-width: 480px" in css_rule(page, ".card") and "margin: 0 auto" in css_rule(page, ".card")                            # 큰 화면에서도 읽기 좋은 폭
        assert "prefers-color-scheme: dark" in page and "color-scheme: light dark" in page                                               # 어두운 화면 설정
        print("1) 모바일 가독성 OK")

        # Scenario 2: 구성 — 제목(작품명, HTML은 이스케이프), 안내 문구, 눈에 띄는 큰 "제외하기" 버튼. 성공하면 완료 문구와 큰 버튼(서비스로 이동/이 탭 닫기)
        assert "<h1>쌍갑포차: &lt;b&gt;1부&lt;/b&gt;</h1>" in page and "<b>1부</b>" not in page.split("<script>")[0]
        assert 'class="question"' in page and "제외됨" in page and '<button id="btn" class="btn btn-primary">제외하기</button>' in page
        script = page.split("<script>")[1]
        assert 'class="btn btn-link"' in script and "서비스로 이동" in script and 'id="close-btn"' in script and 'class="btn btn-secondary"' in script
        assert "✅ 제외되었습니다." in script and 'class="done"' in script and px(css_rule(page, ".done"), "font-size") >= 20                       # 완료 문구도 크게
        assert "background" in css_rule(page, ".btn-primary") and "color" in css_rule(page, ".btn-primary")                                # 주 버튼은 색으로 구분
        print("2) 구성 OK")

        # Scenario 3: 동작은 그대로 — 네이버/카카오 각자의 제외 주소로 POST, 제목/썸네일은 페이로드에 그대로. GET은 아무것도 바꾸지 않는다
        assert 'fetch("/api/naver-list/88800001/exclude"' in page and "http://t/1.jpg" in page and "\\u003cb\\u003e" in page          # 제목의 <b>는 스크립트 안에서 \u003c로 이스케이프돼 들어간다
        assert 'fetch("/api/kakao-webtoons/88800002/exclude"' in kakao.text and "도굴왕" in kakao.text
        assert 'method: "POST"' in page and "res.ok" in script and "실패: " in script and "오류: " in script and "처리 중..." in script
        assert repository.get("88800001") is None and repository.get_kakao_webtoon(88800002) is None                                       # 페이지를 열어도 제외되지 않는다
        print("3) 동작 그대로 OK")

        # Scenario 4: 보안 — 주소의 title 값이나 경로의 작품 번호에 </script>나 따옴표가 들어 있어도 스크립트 밖으로 빠져나가 실행되지 않는다(이 앱은 외부에서 열 수 있다)
        evil = '</script><script>alert(1)</script>'
        for response in (await c.get("/api/webtoons/88800001/exclude-confirm", params={"title": evil}), await c.get("/api/kakao-webtoons/88800002/exclude-confirm", params={"title": evil}),
                         await c.get("/api/webtoons/" + '1%22%3Balert(1)%3B%22' + "/exclude-confirm", params={"title": "x"})):
            body = response.text
            assert response.status_code == 200 and body.count("<script>") == 1 and body.count("</script>") == 1, body[-900:]                  # 스크립트 블록은 하나뿐
            assert "alert(1)</script>" not in body and '";alert(1);"' not in body
        assert "&lt;/script&gt;" in (await c.get("/api/webtoons/88800001/exclude-confirm", params={"title": evil})).text                          # 보이는 제목은 HTML 이스케이프
        print("4) 스크립트 삽입 방어 OK")
    print("ALL OK")

asyncio.run(main())
