"""정적 파일 제공 — index.html이 가리키는 모든 스크립트가 실제로 내려오고, 캐시는 매번 검증하며, 로드 순서 제약을 지킨다."""
import asyncio, re
import httpx
from app import db
db.get_connection()
import app.main as m

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        html = (await c.get("/")).text
        srcs = re.findall(r'<script src="(/[^"]+)"></script>', html)
        assert len(srcs) >= 10 and srcs == sorted(srcs), srcs                                     # 이름 순서 = 로드 순서
        assert srcs[0].endswith("00-core.js") and srcs[-1].endswith("99-main.js")                   # 공용이 맨 앞, 초기화가 맨 뒤
        for src in srcs:
            r = await c.get(src); assert r.status_code == 200 and len(r.text) > 50, src
            assert r.headers["cache-control"] == "no-cache", src                                    # 파일명에 해시가 없으니 매번 검증
        assert (await c.get("/app.js")).status_code == 404                                          # 옛 단일 파일은 없다
        assert (await c.get("/style.css")).status_code == 200
    print("정적 파일 OK:", len(srcs), "개 스크립트")
asyncio.run(main())
print("\n전부 통과")
