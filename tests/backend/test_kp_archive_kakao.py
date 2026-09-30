"""아카이빙 대상에 카카오 웹툰: 등록 후보/등록/표시, 카카오 폴더에서 옮기기(주기/수동/완결처리), 미리보기, 네이버는 그대로."""
import asyncio
import tempfile
import zipfile
from pathlib import Path
import httpx

from app import db, repository, archiver, download_roots, kakao_page_download as kp
db.get_connection()
kp.kakao_cover.fetch_official_cover_bytes = lambda sid, **k: b"\xff\xd8\xffcover"   # 표지는 외부 요청 없이
import app.main as m
from app.config import get_settings

def zipfile_(folder: Path, name: str):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as z: z.writestr("1.jpg", b"x")

async def main():
    st = get_settings(); archive = Path(st.archive_root); archive.mkdir(parents=True, exist_ok=True)
    naver_root = Path(st.download_root); naver_root.mkdir(parents=True, exist_ok=True)
    kakao_root = Path(tempfile.mkdtemp()); repository.set_setting(kp.DOWNLOAD_ROOT_SETTING_KEY, str(kakao_root))     # 카카오는 네이버와 완전히 다른 폴더
    assert download_roots.kakao_root(st) == str(kakao_root) != download_roots.naver_root(st)

    # 카카오: 제목에 금지문자(콜론)가 있는 작품 — 카카오 폴더 이름 규칙(밑줄)으로 저장돼 있다
    repository.upsert_new_kakao_webtoon(61641075, "도굴왕: 엔드라인", status=repository.STATUS_ACTIVE, author_summary="윤쓰, 3B2S")
    kfolder = kakao_root / "도굴왕_ 엔드라인"
    for n in (1, 2, 3): zipfile_(kfolder, f"{n:04d}_{n}화.zip")
    (kfolder / "info.xml").write_text("<ComicInfo/>", encoding="utf-8")
    # 네이버 작품(같은 번호대 id를 써도 섞이지 않는다) + 네이버 폴더의 파일
    repository.upsert_new(title_id="777", title="네이버작품", added_source=repository.SOURCE_MANUAL)
    for n in (1, 2): zipfile_(naver_root / "네이버작품", f"{n:04d} {n}화.zip")

    # ── 1. 해석 ──
    src = archiver.resolve_webtoon_source("kakao_61641075")
    assert src and src.kakao and src.title == "도굴왕: 엔드라인" and src.writer_names == ["윤쓰", "3B2S"]
    n = archiver.resolve_webtoon_source("777"); assert n and not n.kakao and n.title == "네이버작품"
    assert archiver.resolve_webtoon_source("kakao_999") is None and archiver.resolve_webtoon_source("kakao_abc") is None and archiver.resolve_webtoon_source("nope") is None
    print("1) 대상 해석 OK (kakao_<번호>, 네이버와 안 섞임, 없는 것/잘못된 값은 None)")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://test") as c:
        # ── 2. 등록/목록 ──
        dest = "웹툰보관"
        r = await c.post("/api/archive/targets", json={"title_id": "kakao_61641075", "dest_base_path": dest, "dest_type": "local"})
        assert r.status_code == 200 and r.json()["platform"] == "kakao" and r.json()["title_name"] == "도굴왕: 엔드라인" and r.json()["title_id"] == "kakao_61641075", r.text
        assert (await c.post("/api/archive/targets", json={"title_id": "kakao_555", "dest_base_path": dest, "dest_type": "local"})).status_code == 404        # 구독 안 한 작품은 등록 불가
        assert (await c.post("/api/archive/targets", json={"title_id": "777", "dest_base_path": dest, "dest_type": "local"})).json()["platform"] == "naver"
        lst = {t["title_id"]: t for t in (await c.get("/api/archive/targets")).json()}
        assert lst["kakao_61641075"]["platform"] == "kakao" and lst["777"]["platform"] == "naver" and lst["777"]["title_name"] == "네이버작품"
        print("2) 등록/목록 OK (플랫폼 표시, 구독 안 한 카카오 작품은 거부)")

        # ── 3. 미리보기: 카카오 폴더의 파일로 ──
        pv = (await c.post("/api/archive/preview-filename", json={"title_id": "kakao_61641075", "template": "{title} {episode_no}", "source_type": "webtoon"})).json()
        assert pv["original_filename"] == "0003_3화.zip" and pv["rendered_filename"], pv
        print("3) 미리보기 OK (카카오 폴더/카카오 폴더 이름 규칙)")

        # ── 4. 주기 아카이빙: 카카오 폴더에서 옮기되 마지막 회차는 남긴다, 네이버 폴더는 네이버 것만 ──
        moved = archiver.run_periodic_archive(st.archive_root, str(naver_root), "", None, None, None, str(kakao_root))
        assert moved == 3, moved                                                                                    # 카카오 2개 + 네이버 1개
        assert sorted(p.name for p in kfolder.glob("*.zip")) == ["0003_3화.zip"]                                       # 마지막(3화)은 보존
        assert sorted(p.name for p in (naver_root / "네이버작품").glob("*.zip")) == ["0002 2화.zip"]
        dest_dir = archive / dest
        moved_names = sorted(p.name for p in dest_dir.rglob("*.zip"))
        assert len(moved_names) == 3 and any("0001" in n_ or "1화" in n_ for n_ in moved_names), moved_names
        assert any("도굴왕" in str(p) for p in dest_dir.rglob("*.zip")), [str(p) for p in dest_dir.rglob("*")]        # 보관 폴더 아래 작품 폴더
        print("4) 주기 아카이빙 OK (카카오 폴더에서 옮김, 마지막 회차 보존, 네이버 폴더는 별개)")

        # ── 5. 카카오 폴더를 안 넘기면(예전 호출) 네이버 폴더에서 찾으므로 카카오 파일은 건드리지 않는다 ──
        before = sorted(p.name for p in kfolder.glob("*.zip"))
        archiver.run_periodic_archive(st.archive_root, str(naver_root)); assert sorted(p.name for p in kfolder.glob("*.zip")) == before
        print("5) 예전 호출 방식과 호환 OK")

        # ── 6. 수동 실행(완결 처리로 이동): 마지막 회차까지 옮기고 빈 폴더 정리 ──
        moved = archiver.manual_archive_now(st.archive_root, str(naver_root), ["kakao_61641075"], "", None, True, str(kakao_root))
        assert moved >= 1 and not list(kfolder.glob("*.zip"))                                                        # 3화까지 전부
        assert not kfolder.exists() or all(p.name in ("info.xml", "cover.jpg") for p in kfolder.iterdir()), sorted(p.name for p in kfolder.iterdir())   # 폴더에는 zip이 남지 않는다
        print("6) 수동 완결 처리 OK (마지막 회차까지 이동)")

        # ── 7. API 수동 실행 경로에서도 카카오 폴더를 쓴다 ──
        zipfile_(kfolder, "0004_4화.zip"); zipfile_(kfolder, "0005_5화.zip")
        r = await c.post("/api/archive/run", json={"title_ids": ["kakao_61641075"], "full_move": False}); assert r.status_code == 200
        for _ in range(100):
            await asyncio.sleep(0.05)
            from app import job_status
            if job_status.snapshot().get("archive", {}).get("status") not in (None, "running"): break
        assert sorted(p.name for p in kfolder.glob("*.zip")) == ["0005_5화.zip"], sorted(p.name for p in kfolder.glob("*"))
        print("7) API 수동 실행 OK")

        # ── 8. {author}는 네이버처럼 "글" 작가 (그림/원작은 넣지 않는다) ──
        about = {"author_list": [{"name": "김용회", "role": "writer"}, {"name": "김용회", "role": "illustrator"}, {"name": "베르나르 베르베르", "role": "original_author"}]}
        repository.upsert_new_kakao_webtoon(68239972, "개미", status=repository.STATUS_ACTIVE, author_summary="김용회,베르나르 베르베르")
        assert archiver.resolve_webtoon_source("kakao_68239972").writer_names == ["김용회", "베르나르 베르베르"]       # 작품 정보를 받기 전에는 목록의 작가 이름으로 대신
        await kp.write_series_metadata({"title": "개미", "authors": "김용회,베르나르 베르베르"}, 68239972, kakao_root / "개미", None, about)   # 받을 때마다 저장
        assert repository.get_kakao_webtoon(68239972)["writer_names"] == ["김용회"]
        src = archiver.resolve_webtoon_source("kakao_68239972"); assert src.writer_names == ["김용회"]
        from app.archiver import render_archive_filename
        assert render_archive_filename("{author} - {subtitle}", "0001_1화.zip", "개미", src.writer_names) == "김용회 - 1화.zip"        # 원작자/중복 이름이 섞이지 않는다
        await kp.write_series_metadata({"title": "개미"}, 68239972, kakao_root / "개미", None, None)                       # 정보를 못 받은 날에는 기존 값을 지우지 않는다
        assert repository.get_kakao_webtoon(68239972)["writer_names"] == ["김용회"]
        print("8) {author} OK (글 작가만, 정보 전에는 목록 이름, 못 받아도 값 유지)")

        # ── 9. 완결 구독해제 → 자동 이동 대기열 → 마지막 회차까지 이동 (네이버와 같은 규칙) ──
        repository.set_setting("archive_on_finish_unsubscribe", "1")
        pfolder = kakao_root / "도굴왕_ 엔드라인"
        for old_ in pfolder.glob("*.zip"): old_.unlink()                                                             # 앞 단계에서 이미 옮긴 회차와 이름이 겹치면 충돌 정책(건너뜀)이 적용되므로 새 회차로
        for n_ in (11, 12, 13): zipfile_(pfolder, f"{n_:04d}_{n_}화.zip")
        repository.set_kakao_webtoon_status(61641075, repository.STATUS_ACTIVE)
        r = await c.post("/api/kakao-webtoons/61641075/unsubscribe"); assert r.status_code == 200
        assert repository.list_pending_finish_archive() == []                                                        # 완결이 아니면 대기열에 안 넣는다
        repository.set_kakao_webtoon_status(61641075, repository.STATUS_ACTIVE); repository.set_kakao_finished(61641075, True)
        repository.set_setting("archive_on_finish_unsubscribe", None)
        await c.post("/api/kakao-webtoons/61641075/unsubscribe"); assert repository.list_pending_finish_archive() == []   # 설정이 꺼져 있으면 안 넣는다
        repository.set_setting("archive_on_finish_unsubscribe", "1"); repository.set_kakao_webtoon_status(61641075, repository.STATUS_ACTIVE)
        await c.post("/api/kakao-webtoons/61641075/unsubscribe")
        assert repository.list_pending_finish_archive() == ["kakao_61641075"]                                          # 완결 + 설정 켬 → 대기열
        moved = archiver.process_pending_finish_archives(st.archive_root, str(naver_root), "", None, None, None, str(kakao_root))
        assert moved == 3 and not list(pfolder.glob("*.zip")) and repository.list_pending_finish_archive() == []      # 마지막 회차까지 전부, 대기열 비움
        assert not pfolder.exists() or not any(pfolder.iterdir()), sorted(p_.name for p_ in pfolder.iterdir())        # 다 옮기면 빈 다운로드 폴더는 정리(info.xml/표지도 함께 이동)
        assert sorted(p_.name for p_ in (archive / "웹툰보관" / "도굴왕： 엔드라인").glob("001[123]_*.zip")) == ["0011_11화.zip", "0012_12화.zip", "0013_13화.zip"]   # 보관 폴더에 도착
        # 네이버도 같은 도우미를 거쳐 그대로 동작
        repository.mark_finished("777"); repository.set_status("777", repository.STATUS_ACTIVE)
        await c.post("/api/webtoons/777/unsubscribe"); assert repository.list_pending_finish_archive() == ["777"]
        repository.remove_pending_finish_archive("777")
        # 대기열의 카카오 작품이 지워졌으면(없는 작품) 조용히 대기열에서 뺀다
        repository.add_pending_finish_archive("kakao_424242"); archiver.process_pending_finish_archives(st.archive_root, str(naver_root), "", None, None, None, str(kakao_root))
        assert repository.list_pending_finish_archive() == []
        print("9) 완결 자동 이동 OK (완결+설정 켬일 때만 대기열, 마지막 회차까지 이동, 네이버 동일, 없는 작품 정리)")
asyncio.run(main())
print("\n전부 통과")
