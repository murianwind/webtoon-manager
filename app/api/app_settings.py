"""일반 설정: 웹툰 뷰어 서버, 리포트 옵션, 카카오웹툰 관리 켜기, 공개 주소, 작가 자동 등록, 다운로드 폴더."""


import asyncio
import logging
import tempfile
from pathlib import Path

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import (
    download_roots,
    repository,
)
from app import kakao_page_download
from app import webtoon_server_client
from app.file_utils import remove_forbidden_str, remove_forbidden_str_kakao
from app.config import get_settings

from app.api.common import (
    _is_author_auto_register_enabled,
)


log = logging.getLogger(__name__)

router = APIRouter()



class DownloadRootsIn(BaseModel):
    naver: str = ""
    kakao: str = ""


def _download_roots_state() -> dict:
    """설정 화면용: 네이버/카카오페이지 각각 (설정값, 실제로 쓰는 폴더). 카카오 폴더를 비워 두면 네이버 폴더를 쓴다."""
    settings = get_settings()
    naver_effective, kakao_effective = download_roots.naver_root(settings), download_roots.kakao_root(settings)
    return {
        # base: 컨테이너에 마운트된 다운로드 폴더(폴더 찾아보기의 출발점), host_path: 호스트 쪽 실제 경로(알 때만)
        "base": settings.download_root, "host_path": settings.webtoon_download_host_path,
        "naver": {
            "path": repository.get_setting(download_roots.NAVER_ROOT_SETTING_KEY) or "", "effective": naver_effective,
            "effective_host": download_roots.host_path_of(naver_effective, settings), "default": settings.download_root,
        },
        "kakao": {
            "path": repository.get_setting(kakao_page_download.DOWNLOAD_ROOT_SETTING_KEY) or "", "effective": kakao_effective,
            "effective_host": download_roots.host_path_of(kakao_effective, settings), "default": naver_effective,
        },
    }


def _validate_download_folder(raw_path: str) -> str:
    """비어 있으면 "" (기본 폴더로 되돌림). 아니면 이미 있는(컨테이너에 마운트된) 절대 경로의 쓸 수 있는 폴더만 받는다 — 없는
    폴더를 자동으로 만들면 마운트가 안 된 경로일 때 컨테이너 안에만 저장돼서, 컨테이너를 다시 만들면 받은 파일이 사라지기 때문이다."""
    path = raw_path.strip()
    if not path:
        return ""
    folder = Path(path)
    if not folder.is_absolute():
        raise HTTPException(status_code=400, detail="절대 경로로 입력해주세요(예: /webtoon_download_kakao).")
    if not folder.is_dir():
        raise HTTPException(
            status_code=400,
            detail="그 폴더가 없습니다. 컨테이너에 마운트한 경로가 맞는지 확인해주세요(마운트 안 된 경로를 만들면 컨테이너 안에만 저장돼서 자동으로 만들지 않습니다).",
        )
    try:
        with tempfile.NamedTemporaryFile(dir=folder):
            pass
    except OSError:
        raise HTTPException(status_code=400, detail="그 폴더에 파일을 쓸 수 없습니다. 권한을 확인해주세요.")
    return path


@router.get("/settings/download-roots")
async def get_download_roots():
    return await asyncio.to_thread(_download_roots_state)


@router.post("/settings/download-roots")
async def set_download_roots(payload: DownloadRootsIn):
    """네이버/카카오페이지 다운로드 폴더. 폴더를 바꿔도 이미 받은 파일을 옮기지는 않는다(옮기는 건 사용자가 직접). 웹툰 유형
    아카이빙 대상은 실행할 때 새 폴더를 보고, 폴더 유형 대상/일괄 이동은 "다운로드 폴더"(네이버)와 "카카오페이지 다운로드
    폴더"를 각각 고를 수 있다."""
    naver = _validate_download_folder(payload.naver)
    kakao = _validate_download_folder(payload.kakao)
    await asyncio.to_thread(repository.set_setting, download_roots.NAVER_ROOT_SETTING_KEY, naver or None)
    await asyncio.to_thread(repository.set_setting, kakao_page_download.DOWNLOAD_ROOT_SETTING_KEY, kakao or None)
    return await asyncio.to_thread(_download_roots_state)


class AuthorAutoRegisterOut(BaseModel):
    enabled: bool


class AuthorAutoRegisterIn(BaseModel):
    enabled: bool


@router.get("/settings/author-auto-register", response_model=AuthorAutoRegisterOut)
async def get_author_auto_register():
    """구독할 때 그 작품 작가를 '등록된 작가'로 자동 등록할지 여부. 태그는 이 기능이
    없다 — 애초에 태그는 구독 시점에 자동 등록되는 개념 자체가 없기 때문(작가만 해당)."""
    enabled = await asyncio.to_thread(_is_author_auto_register_enabled)
    return AuthorAutoRegisterOut(enabled=enabled)


@router.post("/settings/author-auto-register", response_model=AuthorAutoRegisterOut)
async def set_author_auto_register(payload: AuthorAutoRegisterIn):
    await asyncio.to_thread(
        repository.set_setting, "auto_register_author_on_subscribe", None if payload.enabled else "0"
    )
    return await get_author_auto_register()


class WebtoonServerUrlOut(BaseModel):
    webtoon_server_url: str


class WebtoonServerUrlIn(BaseModel):
    webtoon_server_url: str


@router.get("/settings/webtoon-server", response_model=WebtoonServerUrlOut)
async def get_webtoon_server_url():
    """리포트에 '바로가기' 링크를 붙일 때 조회할 별도 웹툰 뷰어 서버 주소.
    비워두면 링크 없이 제목만 나열한다."""
    value = await asyncio.to_thread(repository.get_setting, "webtoon_server_url")
    return WebtoonServerUrlOut(webtoon_server_url=value or "")


@router.post("/settings/webtoon-server", response_model=WebtoonServerUrlOut)
async def set_webtoon_server_url(payload: WebtoonServerUrlIn):
    await asyncio.to_thread(
        repository.set_setting, "webtoon_server_url", payload.webtoon_server_url.strip() or None
    )
    return await get_webtoon_server_url()


class UnregisteredNewEpisodesSettingOut(BaseModel):
    enabled: bool


class UnregisteredNewEpisodesSettingIn(BaseModel):
    enabled: bool


@router.get("/settings/report-unregistered-new-episodes", response_model=UnregisteredNewEpisodesSettingOut)
async def get_unregistered_new_episodes_setting():
    """다운로드 리포트의 '미등록 웹툰 중 새 에피소드' 섹션 on/off. 값이 아직 없으면
    (기존 사용자 등) 기본은 켜짐 — 이미 나가고 있던 섹션이라 꺼진 채로 조용히
    사라지면 오히려 놓친 것처럼 보일 수 있어서."""
    value = await asyncio.to_thread(repository.get_setting, "report_unregistered_new_episodes_enabled")
    return UnregisteredNewEpisodesSettingOut(enabled=value != "0")


@router.post("/settings/report-unregistered-new-episodes", response_model=UnregisteredNewEpisodesSettingOut)
async def set_unregistered_new_episodes_setting(payload: UnregisteredNewEpisodesSettingIn):
    await asyncio.to_thread(
        repository.set_setting, "report_unregistered_new_episodes_enabled", "1" if payload.enabled else "0"
    )
    return await get_unregistered_new_episodes_setting()


class KakaoWebtoonsEnabledOut(BaseModel):
    enabled: bool


class KakaoWebtoonsEnabledIn(BaseModel):
    enabled: bool


@router.get("/settings/kakao-webtoons-enabled", response_model=KakaoWebtoonsEnabledOut)
async def get_kakao_webtoons_enabled_setting():
    """카카오웹툰 관리(웹툰 전체목록에 표시 + 다운로드 리포트에 새 에피소드 포함) 전체
    on/off. 새로 만든 기능이라 기존 사용자에게 예고 없이 끼어들면 안 되니, 값이
    아직 없으면 기본은 꺼짐."""
    value = await asyncio.to_thread(repository.get_setting, "kakao_webtoons_enabled")
    return KakaoWebtoonsEnabledOut(enabled=value == "1")


@router.post("/settings/kakao-webtoons-enabled", response_model=KakaoWebtoonsEnabledOut)
async def set_kakao_webtoons_enabled_setting(payload: KakaoWebtoonsEnabledIn):
    await asyncio.to_thread(repository.set_setting, "kakao_webtoons_enabled", "1" if payload.enabled else "0")
    return await get_kakao_webtoons_enabled_setting()


class AppPublicBaseUrlOut(BaseModel):
    app_public_base_url: str


class AppPublicBaseUrlIn(BaseModel):
    app_public_base_url: str


@router.get("/settings/app-public-base-url", response_model=AppPublicBaseUrlOut)
async def get_app_public_base_url():
    """이 앱을 바깥에서 접속할 때 쓰는 주소(예: https://webtoon.murian.ddnsfree.com).
    다운로드 리포트의 '목록 제외' 같은, 디스코드에서 눌러서 이 앱의 API를 호출하는
    링크를 만들 때 필요하다 — 컨테이너 안에서는 자기 자신의 외부 주소를 알 방법이
    없어서 직접 설정해줘야 한다. 비워두면 그런 링크 없이 하던 대로 동작한다."""
    value = await asyncio.to_thread(repository.get_setting, "app_public_base_url")
    return AppPublicBaseUrlOut(app_public_base_url=value or "")


@router.post("/settings/app-public-base-url", response_model=AppPublicBaseUrlOut)
async def set_app_public_base_url(payload: AppPublicBaseUrlIn):
    await asyncio.to_thread(
        repository.set_setting, "app_public_base_url", payload.app_public_base_url.strip().rstrip("/") or None
    )
    return await get_app_public_base_url()


@router.get("/webtoon-server/lookup")
async def lookup_webtoon_server_reader_url(title: str, platform: str = "naver"):
    """구독중인 웹툰 카드의 '뷰어에서 보기' 아이콘이 누르는 순간(또는 배경에서 존재
    여부를 확인할 때) 호출한다 — 매번 최신 상태를 물어보는 게 목적이라, 캐시하지
    않고 그때그때 webtoon-server에 직접 조회한다. 뷰어는 실제 디스크 폴더명(':' 등
    금지문자가 치환된 이름) 기준으로 매칭하는데, 그 치환 규칙이 플랫폼마다 다르다 —
    네이버는 이 앱이 직접 받아서 전각 콜론(예: "제목 : 부제" → "제목 ： 부제")으로,
    카카오는 사용자가 쓰는 별도 도구가 받아서 밑줄(예: "제목 : 부제" → "제목_ 부제")로
    치환한다(둘 다 실제로 확인됨). 원본 제목이 아니라 그 치환을 거친 이름으로
    조회해야 한다 — remove_forbidden_str/remove_forbidden_str_kakao 참고."""
    server_url = await asyncio.to_thread(repository.get_setting, "webtoon_server_url")
    if not server_url:
        return {"url": None}
    settings = get_settings()
    safe_title = remove_forbidden_str_kakao(title) if platform == "kakao" else remove_forbidden_str(title)
    async with aiohttp.ClientSession() as session:
        url = await webtoon_server_client.fetch_reader_url(session, server_url, safe_title, settings.request_timeout_seconds)
    return {"url": url}
