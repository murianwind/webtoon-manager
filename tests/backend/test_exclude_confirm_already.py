"""리포트의 "목록 제외" 링크가 여는 확인 페이지 — 이미 목록에서 제외된 웹툰이면 "이미 목록에서 제외된 웹툰입니다"만 보여 주고 제외하기 버튼은 없다(네이버/카카오 공용).
구독 중/구독해제/"목록" 상태이거나 기록이 없는 작품은 예전처럼 제외하기 버튼이 있는 확인 화면이다. 페이지를 열기만 해서는 아무것도 바뀌지 않는다(디스코드가 링크를 미리 열어 본다)."""
import asyncio

import httpx

from app import db, repository
db.get_connection()
import app.main as m

EX, UN, AC = repository.STATUS_EXCLUDED, repository.STATUS_UNSUBSCRIBED, repository.STATUS_ACTIVE
is_confirm = lambda html: 'id="btn"' in html and "제외하기" in html and "fetch(" in html
is_already = lambda html: "이미 목록에서 제외된 웹툰입니다" in html and 'id="btn"' not in html and "제외하기" not in html and "fetch(" not in html

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # 네이버: 기록 없음 / 구독 중 / 구독해제 / "목록" 상태는 확인 화면, 제외됨은 이미 제외 안내
        page = lambda tid, title="제목": c.get(f"/api/webtoons/{tid}/exclude-confirm", params={"title": title})
        r = await page("1", "기록없음"); assert r.status_code == 200 and is_confirm(r.text) and "기록없음" in r.text
        for tid, status in (("2", AC), ("3", UN), ("4", repository.STATUS_UNREGISTERED)):
            repository.upsert_new(tid, f"작품{tid}"); repository.set_status(tid, status)
            assert is_confirm((await page(tid)).text), f"{status}는 확인 화면"
        repository.upsert_new("5", "제외됨작"); repository.set_status("5", EX)
        r = await page("5", "제외됨작"); assert r.status_code == 200 and is_already(r.text) and "제외됨작" in r.text and 'href="/"' in r.text, r.text[-600:]
        assert repository.get("5").status == EX and repository.get("2").status == AC, "페이지를 열기만 해서는 아무것도 바뀌지 않는다"
        # 이미 제외된 작품에 제외 요청이 다시 와도 그대로(오류 없이)
        assert (await c.post("/api/naver-list/5/exclude", json={"title": "제외됨작"})).status_code == 200 and repository.get("5").status == EX
        # 카카오도 같다
        kpage = lambda tid, title="제목": c.get(f"/api/kakao-webtoons/{tid}/exclude-confirm", params={"title": title})
        r = await kpage(501, "카-기록없음"); assert r.status_code == 200 and is_confirm(r.text)
        for tid, status in ((502, AC), (503, UN)):
            repository.upsert_new_kakao_webtoon(tid, f"카{tid}", status=status); assert is_confirm((await kpage(tid)).text)
        repository.upsert_new_kakao_webtoon(504, "카-제외됨", status=EX)
        r = await kpage(504, "카-제외됨"); assert r.status_code == 200 and is_already(r.text) and "카-제외됨" in r.text, r.text[-600:]
        assert repository.get_kakao_webtoon(504)["status"] == EX
        # 제외를 되돌리면(목록으로) 다시 확인 화면
        await c.delete("/api/webtoons/5"); assert is_confirm((await page("5", "제외됨작")).text), "삭제(목록으로) 뒤에는 다시 제외할 수 있다"
asyncio.run(main())
print("ALL OK")
