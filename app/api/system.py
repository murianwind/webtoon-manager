"""백업/복원, 도움말."""


import asyncio
import logging
from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app import (
    repository,
)


log = logging.getLogger(__name__)

router = APIRouter()



@router.get("/help", response_class=HTMLResponse)
async def get_help_page():
    """"도움말" 탭에 보여줄 README를 HTML로 변환해서 준다 — README.md 하나로
    설치 안내/앱 안내를 둘 다 겸하고 있어서, 그중 앱을 쓰는 방법 부분만 이 화면에
    나오면 되지만 굳이 분리하지 않고 전체를 그대로 보여준다(설치 안내도 나중에
    다시 볼 일이 있을 수 있어서)."""
    readme_path = Path(__file__).resolve().parent.parent.parent / "README.md"
    try:
        text = await asyncio.to_thread(readme_path.read_text, encoding="utf-8")
    except FileNotFoundError:
        return HTMLResponse("<p>README.md를 찾을 수 없습니다.</p>", status_code=404)
    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    return HTMLResponse(html)


@router.get("/backup")
async def download_backup():
    return await asyncio.to_thread(repository.export_all)


@router.post("/restore")
async def restore_backup(data: dict):
    try:
        await asyncio.to_thread(repository.restore_all, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 스키마가 안 맞는 백업(필수 컬럼 없음 등)은 DB 예외가 그대로 올라올 수 있다 —
        # 원문 그대로 500으로 흘리는 대신 "복원 실패"로 명확히 감싼다. 트랜잭션은
        # write_transaction이 이미 롤백했으므로 DB는 이전 상태 그대로 안전하다.
        raise HTTPException(status_code=400, detail=f"백업 파일 형식이 올바르지 않아 복원하지 못했습니다: {e}")
    return {"status": "restored"}
