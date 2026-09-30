"""아카이빙: 대상/프리셋/폴더 찾아보기/수동 실행/이력/일괄 이동."""


import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app import (
    download_roots,
    job_status,
    repository,
)
from app import archiver
from app import rclone_client
from app import rclone_updater
from app.config import get_settings

from app.api.common import (
    RetentionDaysIn,
    RetentionDaysOut,
)


log = logging.getLogger(__name__)

router = APIRouter()



class ArchiveTargetOut(BaseModel):
    title_id: str
    title_name: str
    dest_base_path: str
    dest_type: str
    enabled: bool
    source_type: str = "webtoon"
    source_dest_type: str = "local"
    source_path: str = ""
    filename_template_preset_id: int | None = None
    filename_template_preset_name: str | None = None  # None이면 "기본(전역)"
    platform: str = "naver"  # 웹툰 유형 대상의 플랫폼(naver | kakao)


class ArchiveTargetIn(BaseModel):
    title_id: str
    dest_base_path: str
    dest_type: str = "local"
    filename_template_preset_id: int | None = None  # 등록 시점에 바로 프리셋을 지정하고 싶을 때(선택)

    @field_validator("dest_type")
    @classmethod
    def valid_dest_type(cls, v: str) -> str:
        if v not in ("local", "rclone"):
            raise ValueError("dest_type은 local/rclone 중 하나여야 합니다.")
        return v


class FolderArchiveTargetIn(BaseModel):
    display_name: str = ""  # 비우면 원본 폴더명 자동 사용
    source_dest_type: str = "local"
    source_path: str
    dest_base_path: str
    dest_type: str = "local"
    filename_template_preset_id: int | None = None  # 등록 시점에 바로 프리셋을 지정하고 싶을 때(선택)

    @field_validator("source_dest_type", "dest_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in ("local", "rclone"):
            raise ValueError("local/rclone 중 하나여야 합니다.")
        return v


class ApplyPresetIn(BaseModel):
    target_ids: list[str]
    preset_id: int | None = None  # None이면 "기본(전역)"으로 되돌림


def _archive_target_to_out(target) -> ArchiveTargetOut:
    if target.source_type == "folder":
        title_name = target.display_name or archiver._derive_folder_display_name(
            target.source_dest_type, target.source_path
        )
    else:
        source = archiver.resolve_webtoon_source(target.title_id)
        title_name = source.title if source else target.title_id
    preset_name = None
    if target.filename_template_preset_id is not None:
        preset = repository.get_filename_template_preset(target.filename_template_preset_id)
        preset_name = preset.name if preset else None
    return ArchiveTargetOut(
        title_id=target.title_id,
        title_name=title_name,
        dest_base_path=target.dest_base_path,
        dest_type=target.dest_type,
        enabled=target.enabled,
        source_type=target.source_type,
        source_dest_type=target.source_dest_type,
        source_path=target.source_path,
        filename_template_preset_id=target.filename_template_preset_id,
        filename_template_preset_name=preset_name,
        platform="kakao" if target.title_id.startswith(archiver.KAKAO_TARGET_PREFIX) else "naver",
    )


@router.get("/archive/targets", response_model=list[ArchiveTargetOut])
async def list_archive_targets():
    targets = await asyncio.to_thread(repository.list_archive_targets)
    return [_archive_target_to_out(t) for t in targets]


@router.post("/archive/targets", response_model=ArchiveTargetOut)
async def add_archive_target(payload: ArchiveTargetIn):
    settings = get_settings()
    if payload.dest_type == "rclone":
        if not (settings.rclone_config_path and Path(settings.rclone_config_path).is_file()):
            raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    else:
        if not settings.archive_root:
            raise HTTPException(status_code=400, detail="로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다.")
    # 이미 파일이 있는 폴더도 허용한다 — "이미 파일이 있습니다" 경고는 프론트엔드의
    # 폴더 선택기 단계(/archive/folder-check)에서 이미 보여주고 확인받으므로,
    # 여기서는 그 경고를 다시 검사하지 않는다(등록 자체는 항상 그대로 진행).

    if payload.title_id.startswith(archiver.KAKAO_TARGET_PREFIX) and await asyncio.to_thread(archiver.resolve_webtoon_source, payload.title_id) is None:
        raise HTTPException(status_code=404, detail="등록되지 않은 카카오 웹툰입니다(먼저 구독해주세요).")

    if payload.filename_template_preset_id is not None:
        preset = await asyncio.to_thread(repository.get_filename_template_preset, payload.filename_template_preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="존재하지 않는 프리셋입니다.")

    await asyncio.to_thread(
        repository.upsert_archive_target, payload.title_id, payload.dest_base_path, True, payload.dest_type
    )
    if payload.filename_template_preset_id is not None:
        await asyncio.to_thread(repository.set_archive_target_filename_preset, payload.title_id, payload.filename_template_preset_id)
    target = await asyncio.to_thread(repository.get_archive_target, payload.title_id)
    return _archive_target_to_out(target)


@router.post("/archive/folder-targets", response_model=ArchiveTargetOut)
async def add_folder_archive_target(payload: FolderArchiveTargetIn):
    """웹툰 레코드가 없는 폴더(카카오웹툰 등)를 아카이빙 대상으로 등록한다."""
    settings = get_settings()
    if payload.source_dest_type == "rclone" and not (
        settings.rclone_config_path and Path(settings.rclone_config_path).is_file()
    ):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    if payload.dest_type == "rclone" and not (
        settings.rclone_config_path and Path(settings.rclone_config_path).is_file()
    ):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    if payload.dest_type == "local" and not settings.archive_root:
        raise HTTPException(status_code=400, detail="로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다.")
    if payload.filename_template_preset_id is not None:
        preset = await asyncio.to_thread(repository.get_filename_template_preset, payload.filename_template_preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="존재하지 않는 프리셋입니다.")

    target_id = await asyncio.to_thread(
        repository.create_folder_archive_target,
        payload.display_name, payload.source_dest_type, payload.source_path,
        payload.dest_base_path, payload.dest_type,
    )
    if payload.filename_template_preset_id is not None:
        await asyncio.to_thread(repository.set_archive_target_filename_preset, target_id, payload.filename_template_preset_id)
    target = await asyncio.to_thread(repository.get_archive_target, target_id)
    return _archive_target_to_out(target)


@router.post("/archive/folder-targets/{target_id}", response_model=ArchiveTargetOut)
async def update_folder_archive_target(target_id: str, payload: FolderArchiveTargetIn):
    existing = await asyncio.to_thread(repository.get_archive_target, target_id)
    if existing is None or existing.source_type != "folder":
        raise HTTPException(status_code=404, detail="등록된 폴더 대상이 아닙니다.")
    await asyncio.to_thread(
        repository.update_folder_archive_target,
        target_id, payload.display_name, payload.source_dest_type, payload.source_path,
        payload.dest_base_path, payload.dest_type,
    )
    target = await asyncio.to_thread(repository.get_archive_target, target_id)
    return _archive_target_to_out(target)


@router.post("/archive/targets/apply-preset")
async def apply_filename_preset_to_targets(payload: ApplyPresetIn):
    if payload.preset_id is not None:
        preset = await asyncio.to_thread(repository.get_filename_template_preset, payload.preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="존재하지 않는 프리셋입니다.")
    for target_id in payload.target_ids:
        await asyncio.to_thread(repository.set_archive_target_filename_preset, target_id, payload.preset_id)
    return {"status": "applied", "count": len(payload.target_ids)}


@router.post("/archive/targets/{title_id}/enable", response_model=ArchiveTargetOut)
async def enable_archive_target(title_id: str):
    await asyncio.to_thread(repository.set_archive_target_enabled, title_id, True)
    target = await asyncio.to_thread(repository.get_archive_target, title_id)
    if target is None:
        raise HTTPException(status_code=404, detail="등록된 아카이빙 대상이 아닙니다.")
    return _archive_target_to_out(target)


@router.post("/archive/targets/{title_id}/disable", response_model=ArchiveTargetOut)
async def disable_archive_target(title_id: str):
    await asyncio.to_thread(repository.set_archive_target_enabled, title_id, False)
    target = await asyncio.to_thread(repository.get_archive_target, title_id)
    if target is None:
        raise HTTPException(status_code=404, detail="등록된 아카이빙 대상이 아닙니다.")
    return _archive_target_to_out(target)


@router.delete("/archive/targets/{title_id}")
async def remove_archive_target(title_id: str):
    await asyncio.to_thread(repository.delete_archive_target, title_id)
    return {"status": "deleted"}


class FilenamePresetOut(BaseModel):
    id: int
    name: str
    template: str


class FilenamePresetIn(BaseModel):
    name: str
    template: str = ""

    @field_validator("template")
    @classmethod
    def valid_template_tokens(cls, v: str) -> str:
        allowed = {"{title}", "{episode_no}", "{subtitle}", "{page_count}", "{author}"}
        found = re.findall(r"\{[^{}]*\}", v)
        unknown = [tok for tok in found if tok not in allowed]
        if unknown:
            raise ValueError(f"알 수 없는 템플릿 토큰: {', '.join(unknown)} (사용 가능: {', '.join(sorted(allowed))})")
        return v


@router.get("/archive/presets", response_model=list[FilenamePresetOut])
async def list_filename_presets():
    presets = await asyncio.to_thread(repository.list_filename_template_presets)
    return [FilenamePresetOut(id=p.id, name=p.name, template=p.template) for p in presets]


@router.post("/archive/presets", response_model=FilenamePresetOut)
async def create_filename_preset(payload: FilenamePresetIn):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="프리셋 이름을 입력하세요.")
    preset_id = await asyncio.to_thread(repository.create_filename_template_preset, payload.name.strip(), payload.template)
    return FilenamePresetOut(id=preset_id, name=payload.name.strip(), template=payload.template)


@router.post("/archive/presets/{preset_id}", response_model=FilenamePresetOut)
async def update_filename_preset(preset_id: int, payload: FilenamePresetIn):
    existing = await asyncio.to_thread(repository.get_filename_template_preset, preset_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 프리셋입니다.")
    await asyncio.to_thread(repository.update_filename_template_preset, preset_id, payload.name.strip(), payload.template)
    return FilenamePresetOut(id=preset_id, name=payload.name.strip(), template=payload.template)


@router.delete("/archive/presets/{preset_id}")
async def delete_filename_preset(preset_id: int):
    await asyncio.to_thread(repository.delete_filename_template_preset, preset_id)
    return {"status": "deleted"}


class ArchiveSettingsOut(BaseModel):
    default_base_path: str
    default_dest_type: str
    conflict_policy: str
    on_finish_unsubscribe: bool
    filename_template: str
    rclone_available: bool
    local_available: bool


class ArchiveSettingsIn(BaseModel):
    default_base_path: str
    default_dest_type: str = "local"
    conflict_policy: str
    on_finish_unsubscribe: bool
    filename_template: str = ""

    @field_validator("conflict_policy")
    @classmethod
    def valid_policy(cls, v: str) -> str:
        if v not in ("overwrite", "skip", "rename"):
            raise ValueError("conflict_policy는 overwrite/skip/rename 중 하나여야 합니다.")
        return v

    @field_validator("filename_template")
    @classmethod
    def valid_template_tokens(cls, v: str) -> str:
        allowed = {"{title}", "{episode_no}", "{subtitle}", "{page_count}", "{author}"}
        # 사용자가 오타를 낸 토큰(예: {episode}처럼 잘못 쓴 것)을 그냥 글자 그대로
        # 파일명에 남기는 것보다, 저장 시점에 바로 알려주는 게 훨씬 낫다.
        found = re.findall(r"\{[^{}]*\}", v)
        unknown = [tok for tok in found if tok not in allowed]
        if unknown:
            raise ValueError(f"알 수 없는 템플릿 토큰: {', '.join(unknown)} (사용 가능: {', '.join(sorted(allowed))})")
        return v

    @field_validator("default_dest_type")
    @classmethod
    def valid_dest_type(cls, v: str) -> str:
        if v not in ("local", "rclone"):
            raise ValueError("default_dest_type은 local/rclone 중 하나여야 합니다.")
        return v


class PreviewFilenameIn(BaseModel):
    title_id: str = ""  # 웹툰 미리보기용 (source_type="webtoon"일 때)
    template: str = ""
    source_type: str = "webtoon"  # webtoon | folder
    source_dest_type: str = "local"  # source_type="folder"일 때: local | rclone
    source_path: str = ""  # source_type="folder"일 때: 아래 source_local_root 기준 경로 또는 "remote:path"
    source_local_root: str = "archive"  # source_dest_type="local"일 때만: "archive" | "download"


class PreviewFilenameOut(BaseModel):
    original_filename: str | None
    rendered_filename: str | None
    message: str


@router.post("/archive/preview-filename", response_model=PreviewFilenameOut)
async def preview_archive_filename(payload: PreviewFilenameIn):
    settings = get_settings()
    if payload.source_type == "folder":
        if not payload.source_path:
            raise HTTPException(status_code=400, detail="미리볼 폴더를 먼저 선택하세요.")
        local_root = download_roots.local_root_path(payload.source_local_root, settings)
        result = await asyncio.to_thread(
            archiver.preview_filename_for_folder,
            local_root, settings.rclone_config_path,
            payload.source_dest_type, payload.source_path, payload.template,
        )
        return PreviewFilenameOut(**result)

    source = await asyncio.to_thread(archiver.resolve_webtoon_source, payload.title_id)
    if source is None:
        raise HTTPException(status_code=404, detail="웹툰을 찾을 수 없습니다.")
    root = download_roots.kakao_root(settings) if source.kakao else download_roots.naver_root(settings)
    result = await asyncio.to_thread(archiver.preview_filename_for_title, root, source.title, payload.template, source.writer_names, source.kakao)
    return PreviewFilenameOut(**result)


@router.get("/archive/settings", response_model=ArchiveSettingsOut)
async def get_archive_settings():
    settings = get_settings()
    default_base_path = await asyncio.to_thread(archiver.get_default_base_path)
    default_dest_type = await asyncio.to_thread(archiver.get_default_dest_type)
    conflict_policy = await asyncio.to_thread(archiver.get_conflict_policy)
    on_finish = await asyncio.to_thread(archiver.is_finish_unsubscribe_archiving_enabled)
    filename_template = await asyncio.to_thread(archiver.get_filename_template)
    return ArchiveSettingsOut(
        default_base_path=default_base_path or "",
        default_dest_type=default_dest_type,
        conflict_policy=conflict_policy,
        on_finish_unsubscribe=on_finish,
        filename_template=filename_template,
        rclone_available=bool(settings.rclone_config_path) and Path(settings.rclone_config_path).is_file(),
        local_available=bool(settings.archive_root),
    )


@router.post("/archive/settings", response_model=ArchiveSettingsOut)
async def set_archive_settings(payload: ArchiveSettingsIn):
    settings = get_settings()
    if payload.default_dest_type == "local" and payload.default_base_path.strip() and not settings.archive_root:
        raise HTTPException(status_code=400, detail="로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다.")
    if payload.default_dest_type == "rclone" and payload.default_base_path.strip() and not (
        settings.rclone_config_path and Path(settings.rclone_config_path).is_file()
    ):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    await asyncio.to_thread(repository.set_setting, "archive_default_base_path", payload.default_base_path.strip() or None)
    await asyncio.to_thread(repository.set_setting, "archive_default_dest_type", payload.default_dest_type)
    await asyncio.to_thread(repository.set_setting, "archive_conflict_policy", payload.conflict_policy)
    await asyncio.to_thread(
        repository.set_setting, "archive_on_finish_unsubscribe", "1" if payload.on_finish_unsubscribe else None
    )
    await asyncio.to_thread(repository.set_setting, "archive_filename_template", payload.filename_template.strip() or None)
    return await get_archive_settings()


@router.get("/archive/rclone/remotes")
async def list_rclone_remotes():
    settings = get_settings()
    if not (settings.rclone_config_path and Path(settings.rclone_config_path).is_file()):
        return {"remotes": []}
    try:
        remotes = await asyncio.to_thread(rclone_client.list_remotes, settings.rclone_config_path)
    except rclone_client.RcloneError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"remotes": remotes}


@router.get("/archive/rclone/folders")
async def list_rclone_folders(remote: str, path: str = ""):
    settings = get_settings()
    if not (settings.rclone_config_path and Path(settings.rclone_config_path).is_file()):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    try:
        folders = await asyncio.to_thread(rclone_client.list_folders, settings.rclone_config_path, remote, path)
    except rclone_client.RcloneError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"remote": remote, "path": path, "folders": folders}


@router.get("/archive/folder-check")
async def check_folder_selectable(dest_type: str, path: str, remote: str = ""):
    """폴더 하나가 이미 파일을 갖고 있어서 선택 불가능한지 확인한다. 목록을 보여줄
    때 모든 하위 폴더를 한꺼번에 확인하면(예전 방식) 폴더가 많을수록 느려지고,
    rclone은 폴더 개수만큼 원격에 개별 요청을 보내야 해서 특히 느려지는 문제가
    있었다 — 그래서 사용자가 실제로 선택하려는 폴더 딱 하나에 대해서만, 클릭한
    시점에 이 엔드포인트로 확인한다."""
    settings = get_settings()
    if dest_type == "rclone":
        if not (settings.rclone_config_path and Path(settings.rclone_config_path).is_file()):
            raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
        try:
            selectable = await asyncio.to_thread(
                archiver.is_folder_selectable_as_dest_rclone, settings.rclone_config_path, f"{remote}:{path}"
            )
        except rclone_client.RcloneError as e:
            raise HTTPException(status_code=502, detail=str(e))
    else:
        if not settings.archive_root:
            raise HTTPException(status_code=400, detail="로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다.")
        selectable = await asyncio.to_thread(archiver.is_folder_selectable_as_dest, settings.archive_root, path)
    return {"selectable": selectable}


class CreateRcloneFolderIn(BaseModel):
    remote: str
    path: str


@router.post("/archive/rclone/folders")
async def create_rclone_folder(payload: CreateRcloneFolderIn):
    settings = get_settings()
    if not (settings.rclone_config_path and Path(settings.rclone_config_path).is_file()):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    try:
        await asyncio.to_thread(rclone_client.create_folder, settings.rclone_config_path, payload.remote, payload.path)
    except rclone_client.RcloneError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"remote": payload.remote, "path": payload.path}


@router.get("/archive/folders")
async def list_archive_folders(path: str = "", local_root: str = "archive"):
    """ARCHIVE_ROOT(기본) 또는 DOWNLOAD_ROOT(local_root="download") 기준 하위 폴더
    목록을 보여준다 — 폴더 찾아보기 UI용. 각 폴더가 이미 파일을 갖고 있어서
    선택 불가능한지도 같이 알려준다.

    rclone 마운트 같은 특수 폴더는 존재는 하는데 목록조회(iterdir)나 종류 확인(is_dir)
    자체가 예외를 던지는 경우가 실제로 있어서, 항목 하나하나 개별 예외 처리를 한다 —
    문제있는 항목 하나 때문에 폴더 찾아보기 전체가 500으로 죽으면 안 되기 때문."""
    if local_root not in download_roots.LOCAL_ROOT_NAMES:
        raise HTTPException(status_code=400, detail="local_root는 archive, download, kakao_download, download_base 중 하나여야 합니다.")
    settings = get_settings()
    root_dir = download_roots.local_root_path(local_root, settings)
    if not root_dir:
        detail = "로컬 아카이빙 경로(ARCHIVE_ROOT)" if local_root == "archive" else "다운로드 경로(DOWNLOAD_ROOT)"
        raise HTTPException(status_code=400, detail=f"{detail}가 설정되어 있지 않습니다.")
    root = Path(root_dir).resolve()
    target = (root / path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=400, detail="잘못된 경로입니다.")
    if not target.exists():
        return {"path": path, "folders": []}

    folders = []
    try:
        entries = sorted(target.iterdir())
    except OSError as e:
        log.error("폴더 목록 조회 실패 (%s): %s", target, e)
        raise HTTPException(
            status_code=502,
            detail=f"이 폴더의 목록을 읽을 수 없습니다 (마운트가 불안정할 수 있습니다): {e}",
        )

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            rel = str(entry.relative_to(root))
            folders.append({"name": entry.name, "path": rel})
        except OSError as e:
            log.warning("폴더 항목 확인 실패, 건너뜀 (%s): %s", entry, e)
            continue

    return {"path": path, "folders": folders}


class CreateFolderIn(BaseModel):
    path: str
    root: str = "archive"  # "archive"(ARCHIVE_ROOT) | "download"(DOWNLOAD_ROOT)

    @field_validator("root")
    @classmethod
    def root_must_be_known(cls, v: str) -> str:
        if v not in download_roots.LOCAL_ROOT_NAMES:
            raise ValueError("root는 archive, download, kakao_download, download_base 중 하나여야 합니다.")
        return v


@router.post("/archive/folders")
async def create_archive_folder(payload: CreateFolderIn):
    settings = get_settings()
    root_dir = download_roots.local_root_path(payload.root, settings)
    if not root_dir:
        detail = "로컬 아카이빙 경로(ARCHIVE_ROOT)" if payload.root == "archive" else "다운로드 경로(DOWNLOAD_ROOT)"
        raise HTTPException(status_code=400, detail=f"{detail}가 설정되어 있지 않습니다.")
    root = Path(root_dir).resolve()
    target = (root / payload.path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=400, detail="잘못된 경로입니다.")
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    return {"path": payload.path}


class ArchiveRunIn(BaseModel):
    title_ids: list[str] = []
    full_move: bool = False


@router.post("/archive/run")
async def run_archive_now(payload: ArchiveRunIn):
    settings = get_settings()
    job_status.start("archive")

    async def _run():
        try:
            update_result = await rclone_updater.check_and_update()
            job_status.log_line("archive", update_result)
        except Exception as e:
            job_status.log_line("archive", f"rclone 업데이트 확인 중 오류(무시하고 계속): {e}")

        try:
            if payload.title_ids:
                moved = await asyncio.to_thread(
                    archiver.manual_archive_now, settings.archive_root, download_roots.naver_root(settings), payload.title_ids, settings.rclone_config_path,
                    lambda msg: job_status.log_line("archive", msg), payload.full_move, download_roots.kakao_root(settings),
                )
                job_status.log_line("archive", f"{moved}개 파일 이동 완료")
            else:
                all_ids = [t.title_id for t in repository.list_archive_targets() if t.enabled]
                moved = await asyncio.to_thread(
                    archiver.manual_archive_now, settings.archive_root, download_roots.naver_root(settings), all_ids, settings.rclone_config_path,
                    lambda msg: job_status.log_line("archive", msg), False, download_roots.kakao_root(settings),
                )
                job_status.log_line("archive", f"지정 웹툰 {moved}개 파일 이동 완료")

                pending_moved = await asyncio.to_thread(
                    archiver.process_pending_finish_archives, settings.archive_root, download_roots.naver_root(settings), settings.rclone_config_path,
                    lambda msg: job_status.log_line("archive", msg), None, None, download_roots.kakao_root(settings),
                )
                job_status.log_line("archive", f"완결 구독해제 대기열 {pending_moved}개 파일 이동 완료")

            job_status.finish("archive", success=True)
        except Exception as e:
            job_status.log_line("archive", f"오류: {e}")
            job_status.finish("archive", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/archive/history")
async def get_archive_history(page: int = 1):
    items, total = await asyncio.to_thread(repository.list_archive_history, page)
    return {"items": items, "total": total, "page": page, "page_size": 30}


@router.delete("/archive/history")
async def clear_archive_history():
    await asyncio.to_thread(repository.clear_archive_history)
    return {"status": "cleared"}


@router.delete("/archive/history/{entry_id}")
async def delete_archive_history_entry(entry_id: int):
    await asyncio.to_thread(repository.delete_archive_history_entry, entry_id)
    return {"status": "deleted"}


_KEY_ARCHIVE_HISTORY_RETENTION_DAYS = "archive_history_retention_days"


@router.get("/archive/history/retention-days", response_model=RetentionDaysOut)
async def get_archive_history_retention_days():
    value = await asyncio.to_thread(repository.get_setting, _KEY_ARCHIVE_HISTORY_RETENTION_DAYS)
    return RetentionDaysOut(retention_days=int(value) if value else 0)


@router.post("/archive/history/retention-days", response_model=RetentionDaysOut)
async def set_archive_history_retention_days(payload: RetentionDaysIn):
    await asyncio.to_thread(
        repository.set_setting,
        _KEY_ARCHIVE_HISTORY_RETENTION_DAYS,
        str(payload.retention_days) if payload.retention_days > 0 else None,
    )
    return await get_archive_history_retention_days()


class BulkMoveIn(BaseModel):
    source_type: str = "local"  # "local" | "rclone"
    source_path: str            # local: 아래 source_local_root 기준 상대경로 / rclone: "remote:path"
    source_local_root: str = "archive"  # local일 때만: "archive"(ARCHIVE_ROOT) | "download"(DOWNLOAD_ROOT)
    dest_type: str = "local"
    dest_path: str
    dest_local_root: str = "archive"
    filename_template_preset_id: int | None = None  # None이면 파일명을 안 건드리고 그대로 이동
    regenerate_kakao_cover: bool = False  # 켜면 원본이 카카오페이지 작품이면(info.xml 판단) 공식 표지를 새로 받아서 교체

    @field_validator("source_type", "dest_type")
    @classmethod
    def type_must_be_known(cls, v: str) -> str:
        if v not in ("local", "rclone"):
            raise ValueError("source_type/dest_type은 local 또는 rclone이어야 합니다.")
        return v

    @field_validator("source_local_root", "dest_local_root")
    @classmethod
    def local_root_must_be_known(cls, v: str) -> str:
        if v not in download_roots.LOCAL_ROOT_NAMES:
            raise ValueError("source_local_root/dest_local_root는 archive, download, kakao_download 중 하나여야 합니다.")
        return v


@router.post("/archive/bulk-move")
async def bulk_move(payload: BulkMoveIn):
    """다른 아카이빙 잡(주기/완결/수동)과 동일하게, 즉시 응답하고 실제 이동은
    백그라운드에서 진행하며 job_status로 진행 상황을 남긴다 — 파일이 많으면
    수 분 걸릴 수 있는데, 예전처럼 응답이 올 때까지 화면에 아무 표시도 없으면
    "되고 있는 건지조차" 알 수 없다는 문제가 실제로 있었다."""
    settings = get_settings()
    if "rclone" in (payload.source_type, payload.dest_type) and not (
        settings.rclone_config_path and Path(settings.rclone_config_path).is_file()
    ):
        raise HTTPException(status_code=400, detail="rclone 설정 파일이 등록되어 있지 않습니다.")
    source_local_root = download_roots.local_root_path(payload.source_local_root, settings)
    dest_local_root = download_roots.local_root_path(payload.dest_local_root, settings)
    if payload.source_type == "local" and not source_local_root:
        raise HTTPException(status_code=400, detail="선택한 원본 로컬 경로가 설정되어 있지 않습니다.")
    if payload.dest_type == "local" and not dest_local_root:
        raise HTTPException(status_code=400, detail="선택한 목적지 로컬 경로가 설정되어 있지 않습니다.")

    filename_template = ""
    if payload.filename_template_preset_id is not None:
        preset = await asyncio.to_thread(repository.get_filename_template_preset, payload.filename_template_preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="존재하지 않는 프리셋입니다.")
        filename_template = preset.template

    job_status.start("bulk_move")

    async def _run():
        try:
            moved = await asyncio.to_thread(
                archiver.bulk_move_folder,
                source_local_root, dest_local_root, settings.rclone_config_path,
                payload.source_type, payload.source_path,
                payload.dest_type, payload.dest_path,
                lambda msg: job_status.log_line("bulk_move", msg), filename_template,
                payload.regenerate_kakao_cover,
            )
            # 이력은 이제 bulk_move_folder 안에서 파일마다 한 줄씩 직접 남긴다
            # (주기/수동/완결 이동과 동일한 단위) — 여기서 요약 한 줄을 따로 더
            # 남기면 파일 단위 기록과 중복/불일치하므로 더 이상 남기지 않는다.
            job_status.log_line("bulk_move", f"완료 — {moved}개 파일 이동")
            job_status.finish("bulk_move", success=True)
        except ValueError as e:
            job_status.log_line("bulk_move", f"오류: {e}")
            job_status.finish("bulk_move", success=False)
        except archiver.rclone_client.RcloneError as e:
            job_status.log_line("bulk_move", f"rclone 오류: {e}")
            job_status.finish("bulk_move", success=False)
        except Exception as e:
            job_status.log_line("bulk_move", f"예상치 못한 오류: {e}")
            job_status.finish("bulk_move", success=False)

    asyncio.create_task(_run())
    return {"status": "started"}
