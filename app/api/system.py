"""백업/복원, 도움말."""


import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import backup_restore, help_page, kakao_catalog, kakao_records, repository
from app import scheduler as scheduler_mod
from app.config import get_settings


log = logging.getLogger(__name__)

router = APIRouter()



@router.get("/help", response_class=HTMLResponse)
async def get_help_page():
    """"도움말" 탭에 보여줄 README(일반 사용자 설명서)를 목차가 붙은 HTML로 바꿔서 준다. 변환 규칙은 app/help_page.py."""
    readme_path = Path(__file__).resolve().parent.parent.parent / "README.md"
    try:
        text = await asyncio.to_thread(readme_path.read_text, encoding="utf-8")
    except FileNotFoundError:
        return HTMLResponse("<p>README.md를 찾을 수 없습니다.</p>", status_code=404)
    return HTMLResponse(help_page.render_help(text))


@router.get("/backup")
async def download_backup():
    return await asyncio.to_thread(repository.export_all)


@router.post("/restore")
async def restore_backup(data: dict, request: Request):
    """백업으로 데이터를 복원한다. 복원이 끝나면 ① 저장된 스케줄을 바로 적용하고(재시작을 기다리지 않게) ② 카카오가 켜져 있으면
    전체목록을 새로 채운 뒤, 복원한 환경을 점검한 결과(warnings: 확인할 것 / reenter: 다시 입력할 것)를 돌려준다."""
    try:
        report = await asyncio.to_thread(backup_restore.restore_backup, data, get_settings())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 스키마가 안 맞는 백업(필수 컬럼 없음 등)은 DB 예외가 그대로 올라올 수 있다 —
        # 원문 그대로 500으로 흘리는 대신 "복원 실패"로 명확히 감싼다. 트랜잭션은
        # write_transaction이 이미 롤백했으므로 DB는 이전 상태 그대로 안전하다.
        raise HTTPException(status_code=400, detail=f"백업 파일 형식이 올바르지 않아 복원하지 못했습니다: {e}")

    # 예전 백업에는 받은 회차 기록이 없다 — 복원한 폴더가 있으면 그 폴더와 아카이빙 이력으로 만든다(없으면 첫 자동 다운로드 때)
    await asyncio.to_thread(kakao_records.backfill_and_log, get_settings())
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await asyncio.to_thread(scheduler_mod.reschedule_all, scheduler)
    if await asyncio.to_thread(repository.get_setting, "kakao_webtoons_enabled") == "1":
        kakao_catalog.start_refresh()  # 복원한 환경은 저장돼 있던 목록 캐시가 비어 있거나 예전 것이라 새로 채운다(화면은 기다리지 않는다)
    return report
