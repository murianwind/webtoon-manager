"""카카오페이지 자동 다운로드(run_download) — 한 작품 한 번의 실행 결과 전체를 시나리오별로 고정한다(골든 파일).
결과(RunResult), 진행 문구, 폴더에 남는 파일, 저장된 받은 기록을 한꺼번에 본다. 시나리오: 무료 회차 받기와 개수 제한, 기다무로 여는 회차(자기 번호 차례), 기다무 충전 중/불가 회차 건너뛰기,
받기 실패 시 그 회차에서 멈춤, 대여권(켠 경우), 폴더 규칙(받은 파일 있음/기록/표식/받지 않은 것으로 기록), 완결 표시, 제목 지정, 목록 못 읽음.
골든 파일: tests/fixtures/kakao_run_download_golden.json (임시 폴더 경로는 <ROOT>로). 결과 모양을 일부러 바꿀 때만 UPDATE_GOLDEN=1로 다시 만든다."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from app import db, kakao_cover, repository, waitfree_sync
db.get_connection()
from app import kakao_page_download as kp
from fake_kakao_rental_site import JPG, Site, item

_o = asyncio.sleep
async def _ns(x): await _o(0)
asyncio.sleep = _ns
kakao_cover.fetch_official_cover_bytes = lambda sid, **k: JPG
GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "kakao_run_download_golden.json"

results = {}
def run(name, site, *, series_id=1, title=None, max_episodes=5, rental=False, prepare=None, none_listing=False, tracked=True):
    root = tempfile.mkdtemp(); msgs = []
    if tracked:   # 이 프로그램에 등록된 작품(받은 회차 기록을 저장할 수 있다) — 등록 안 된 작품(수동으로만 쓰는 경우)은 기록을 못 남겨서 폴더만 본다
        repository.upsert_new_kakao_webtoon(series_id, "작품"); waitfree_sync.remember(series_id, "2099-01-01T00:00:00+09:00")   # 저장된 다음 충전 시각(기다무로 열 회차가 없으면 지워진다)
    if prepare: prepare(root)
    client = kp.KakaoPageClient(site, {}, 10)
    if none_listing:
        async def no_listing(sid): return None
        client.list_episodes = no_listing
    result = asyncio.run(kp.run_download(client, series_id=series_id, title=title, download_root=root, max_episodes=max_episodes, on_progress=msgs.append, use_rental_tickets=rental))
    folders = sorted(p.name for p in Path(root).iterdir())
    files = sorted(p.name for p in (Path(root) / folders[0]).iterdir()) if folders else []
    results[name] = json.loads(json.dumps({
        "downloaded": result.downloaded, "items": result.downloaded_items, "failed": result.failed, "mode": result.plan.mode, "to_download": [e.number for e in result.plan.to_download],
        "locked": [e.number for e in result.plan.locked], "title": result.title, "ticket_used": result.ticket_used, "finished": result.finished, "waitfree_supported": result.waitfree_supported,
        "rental_used": result.rental_used, "rental_remaining": result.rental_remaining, "rental_skip_mode": result.rental_skip_mode, "error": result.error, "caught_up": getattr(result.caught_up_episode, "number", None), "ticket_needed": getattr(result.ticket_needed, "number", None), "nothing_left": result.nothing_left,
        "messages": msgs, "folders": folders, "files": [f for f in files if f.endswith(".zip")], "has_metadata": any(f in files for f in ("info.xml", "cover.jpg")),
        "recorded": sorted(repository.get_kakao_downloaded_numbers(series_id) or []), "waitfree_next_at": (repository.get_kakao_webtoon(series_id) or {}).get("waitfree_next_at"),
    }, ensure_ascii=False).replace(root, "<ROOT>"))
    return result

free = lambda n: item(n, free=True)
# 1. 새 폴더, 무료 회차 — 개수 제한(max_episodes)까지만 번호순으로
run("무료 개수 제한", Site([free(1), free(2), free(3), free(4)]), max_episodes=2)
run("무료 전부", Site([free(1), free(2), free(3)]), series_id=2, max_episodes=10)
# 2. 기다무로 여는 회차 — 충전돼 있으면 자기 번호 차례에 열고 받는다(한 장뿐이라 그 뒤 잠긴 회차는 대기)
run("기다무로 열기", Site([free(1), item(2), free(3), item(4)], waitfree=True), series_id=3)
run("기다무 충전 중이면 건너뜀", Site([free(1), item(2), free(3)], waitfree=False), series_id=4)
run("기다무 불가 회차 건너뜀", Site([free(1), item(2, blocked=True), free(3)], waitfree=True), series_id=5)
run("기다무 없는 작품", Site([free(1), item(2), free(3)], waitfree=True, series_waitfree=False), series_id=6)
# 3. 받기 실패 — 그 회차에서 멈춘다(뒤 회차는 받지 않는다)
run("받기 실패에서 멈춤", Site([free(1), free(2), free(3)], no_image=[5002]), series_id=7)
run("기다무로 연 회차 받기 실패", Site([free(1), item(2), free(3)], waitfree=True, no_image=[5002]), series_id=8)
# 4. 대여권 — 켠 경우에만, 기다무로 못 여는 잠긴 회차를 보유 수까지 번호순으로
run("대여권 사용", Site([free(1), item(2, blocked=True), item(3, blocked=True), item(4, blocked=True)], rental=2), series_id=9, rental=True)
run("대여권 꺼짐", Site([free(1), item(2, blocked=True)], rental=2), series_id=10, rental=False)
run("대여권 받기 실패", Site([free(1), item(2, blocked=True), item(3, blocked=True)], rental=2, no_image=[5002]), series_id=11, rental=True)
run("받기 실패면 대여권 안 씀", Site([free(1), item(2, blocked=True)], rental=2, no_image=[5001]), series_id=21, rental=True)
run("개수 제한이 대여권에도", Site([free(1), free(2), item(3, blocked=True)], rental=2), series_id=12, rental=True, max_episodes=2)
# 5. 폴더 규칙 — 받은 파일이 있으면 그 뒤만, 받은 기록이 있으면 기록 기준, 파일 하나면 표식, 받지 않은 것으로 기록한 회차는 다시
def with_files(*numbers):
    def prepare(root):
        folder = kp.series_folder(root, "작품"); folder.mkdir(parents=True, exist_ok=True)
        for n in numbers: (folder / f"{n}_{n}화.zip").write_bytes(b"x")
    return prepare
run("받은 파일이 여럿(누락 비교)", Site([free(i) for i in range(1, 7)]), series_id=13, prepare=with_files(1, 2, 4), tracked=False)
run("파일 하나(표식)", Site([free(i) for i in range(1, 6)]), series_id=14, prepare=with_files(3), tracked=False)
run("받은 파일이 여럿(등록된 작품)", Site([free(i) for i in range(1, 7)]), series_id=22, prepare=with_files(1, 2, 4))
run("파일 하나(등록된 작품: 기록이 생긴다)", Site([free(i) for i in range(1, 6)]), series_id=23, prepare=with_files(3))
repository.upsert_new_kakao_webtoon(15, "작품"); repository.set_kakao_downloaded_numbers(15, [1, 2, 3])
run("폴더 없이 받은 기록만", Site([free(i) for i in range(1, 6)]), series_id=15)
repository.upsert_new_kakao_webtoon(16, "작품"); repository.set_kakao_pending_numbers(16, [1])
run("받지 않은 것으로 기록", Site([free(i) for i in range(1, 5)]), series_id=16, prepare=with_files(3))
# 6. 그 밖 — 완결 표시, 제목 지정, 목록 못 읽음, 개수 0
run("완결 작품", Site([free(1)], on_issue="N"), series_id=17)
run("제목 지정", Site([free(1)]), series_id=18, title="내가 정한 제목")
run("목록 못 읽음", Site([free(1)]), series_id=19, none_listing=True)
run("개수 0", Site([free(1), free(2)]), series_id=20, max_episodes=0)

if os.environ.get("UPDATE_GOLDEN") == "1":
    GOLDEN.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"); print("골든 파일을 새로 만들었다:", GOLDEN)
else:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(results) == set(golden), set(results) ^ set(golden)
    for name in golden:
        assert results[name] == golden[name], f"{name}: 결과가 골든과 다르다\n  실제: {json.dumps(results[name], ensure_ascii=False)[:700]}\n  골든: {json.dumps(golden[name], ensure_ascii=False)[:700]}"
        print("  ✓", name)
    print("ALL OK")
