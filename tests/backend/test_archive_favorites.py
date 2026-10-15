"""보관할 폴더 즐겨찾기 — 폴더 선택기에서 자주 쓰는 폴더를 등록해 두고 버튼으로 바로 여는 기능의 저장소/API.

- 즐겨찾기는 (종류 local/rclone, 루트(로컬 루트 이름 또는 rclone 원격 이름), 경로)로 정해지고, 같은 폴더를 다시 추가하면 새로 만들지 않고 기존 것을 돌려준다.
- 이름은 비우면 자동("원격 · 폴더 이름", 로컬은 "로컬 · 폴더 이름", 최상위는 "최상위")으로 붙는다.
- 설정 테이블에 JSON으로 저장되어 백업/복원에 함께 들어간다. 최대 30개.
"""
import asyncio
import json

import httpx

from app import db, repository
db.get_connection()
import app.main as m

KEY = repository.FOLDER_FAVORITES_KEY


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        add = lambda **body: c.post("/api/archive/favorites", json=body)
        # Scenario 1: 처음에는 비어 있다
        assert (await c.get("/api/archive/favorites")).json() == []
        # Scenario 2: rclone 폴더 추가 — 이름 자동, 목록과 설정 테이블에 남는다
        r = await add(dest_type="rclone", root="onedrive", path="개인 자료/Webtoon/02 시리즈")
        assert r.status_code == 200, r.text
        fav = r.json()
        assert fav["label"] == "onedrive · 02 시리즈" and fav["dest_type"] == "rclone" and fav["root"] == "onedrive" and fav["path"] == "개인 자료/Webtoon/02 시리즈" and fav["id"]
        assert [f["id"] for f in (await c.get("/api/archive/favorites")).json()] == [fav["id"]]
        assert json.loads(repository.get_setting(KEY))[0]["path"] == "개인 자료/Webtoon/02 시리즈"
        # Scenario 3: 같은 폴더를 다시 추가하면 기존 것을 돌려준다(중복 없음), 경로 표기(앞뒤 슬래시, 역슬래시)가 달라도 같은 폴더
        again = (await add(dest_type="rclone", root="onedrive", path="/개인 자료\\Webtoon/02 시리즈/")).json()
        assert again["id"] == fav["id"] and len((await c.get("/api/archive/favorites")).json()) == 1
        # Scenario 4: 로컬 폴더("로컬 · 이름"), 최상위("최상위"), 직접 정한 이름(앞뒤 공백 제거)
        local = (await add(dest_type="local", root="archive", path="카카오")).json()
        assert local["label"] == "로컬 · 카카오" and local["root"] == "archive"
        root_level = (await add(dest_type="rclone", root="PLEX", path="")).json()
        assert root_level["label"] == "PLEX · 최상위" and root_level["path"] == ""
        named = (await add(dest_type="rclone", root="onedrive", path="개인 자료/Webtoon/03 완결", label="  완결 보관함  ")).json()
        assert named["label"] == "완결 보관함"
        assert [f["label"] for f in (await c.get("/api/archive/favorites")).json()] == ["onedrive · 02 시리즈", "로컬 · 카카오", "PLEX · 최상위", "완결 보관함"]     # 추가한 순서
        # Scenario 5: 잘못된 값은 422(입력 검증) — 종류, 빈 루트, 상위로 벗어나는 경로(..), 너무 긴 이름
        assert (await add(dest_type="ftp", root="x", path="a")).status_code == 422
        assert (await add(dest_type="rclone", root="  ", path="a")).status_code == 422
        assert (await add(dest_type="rclone", root="onedrive", path="a/../../b")).status_code == 422
        assert (await add(dest_type="rclone", root="onedrive", path="a", label="가" * 61)).status_code == 422
        # Scenario 6: 삭제 — 그 항목만 빠지고 순서는 유지, 없는 번호는 404
        assert (await c.delete(f"/api/archive/favorites/{local['id']}")).status_code == 200
        assert [f["label"] for f in (await c.get("/api/archive/favorites")).json()] == ["onedrive · 02 시리즈", "PLEX · 최상위", "완결 보관함"]
        assert (await c.delete("/api/archive/favorites/없는번호")).status_code == 404
        # Scenario 7: 설정 백업에 들어가고 복원하면 돌아온다(비밀/캐시 설정이 아니라 일반 설정으로 저장)
        snapshot = json.loads(json.dumps(repository.export_all()))
        assert any(row["key"] == KEY for row in snapshot["settings"])
        repository.set_setting(KEY, "[]")
        assert (await c.get("/api/archive/favorites")).json() == []
        for row in snapshot["settings"]:
            if row["key"] == KEY: repository.set_setting(KEY, row["value"])
        assert len((await c.get("/api/archive/favorites")).json()) == 3
        # Scenario 8: 최대 30개 — 넘으면 400과 안내(기존 것은 그대로)
        repository.set_setting(KEY, "[]")
        for i in range(30): assert (await add(dest_type="rclone", root="onedrive", path=f"폴더{i}")).status_code == 200
        over = await add(dest_type="rclone", root="onedrive", path="폴더30")
        assert over.status_code == 400 and "30" in over.json()["detail"], over.text
        assert len((await c.get("/api/archive/favorites")).json()) == 30
        # 가득 찬 상태에서도 이미 있는 폴더를 다시 추가하는 것은 문제없다(새로 만들지 않으니까)
        assert (await add(dest_type="rclone", root="onedrive", path="폴더0")).status_code == 200
        # Scenario 9: 설정 값이 깨져 있어도(수동 편집 등) 목록은 빈 목록으로 열린다
        repository.set_setting(KEY, "이건 JSON이 아님")
        assert (await c.get("/api/archive/favorites")).json() == []
    print("ALL OK")

asyncio.run(main())
