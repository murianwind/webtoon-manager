"""아카이빙 파일 이름 — zip 이름 해석, 이름 틀 렌더링, 미리보기.
"""

import logging
import re
import zipfile
from pathlib import Path

from app import rclone_client
from app.file_utils import remove_forbidden_str
from app.zipper import _LEADING_DIGITS_RE, _clean_name

from app.archiver.transfer import (
    _derive_folder_display_name,
    _local_archive_path,
    _parse_rclone_target,
    _title_folder,
)

log = logging.getLogger(__name__)

def _parse_zip_filename_for_template(stem: str, title_name: str) -> tuple[str, str] | None:
    """zip 파일명(확장자 제외)이 '{일련번호} {제목} {부제목}' 구조라고 가정하고
    (회차 번호 문자열 그대로, 부제목)을 뽑는다. 회차 번호는 원래 문자열 그대로
    반환한다(0채움 자릿수 등 원본 표기를 그대로 유지하기 위함 — 정수로 변환하지 않음).
    제목이 파일명 구조와 안 맞으면(수동으로 넣은 파일 등) None을 반환해서, 호출부가
    파일명 템플릿을 적용하지 않고 원본 파일명을 그대로 쓰게 한다."""
    match = _LEADING_DIGITS_RE.match(stem)
    if not match:
        return None
    episode_no = match.group(1)
    rest = stem[match.end():].lstrip()

    # zip 파일명 속 제목은 다운로드 시(remove_forbidden_str) + 압축 시(_clean_name)
    # 두 번 치환을 거친 상태이므로, 비교 대상 제목도 같은 치환을 거쳐야 맞아떨어진다.
    cleaned_title = _clean_name(remove_forbidden_str(title_name))
    for candidate in (title_name, cleaned_title):
        if rest.startswith(candidate):
            subtitle = rest[len(candidate):].strip()
            if subtitle:
                return episode_no, subtitle
    return None


_KAKAO_STYLE_FILENAME_RE = re.compile(r"^(\d+)_(.+?)(?:#(\d+))?$")


def _parse_kakao_style_filename(stem: str) -> tuple[str, str, int | None] | None:
    """'{번호}_{부제목}' 또는 '{번호}_{부제목}#{페이지수}' 구조(제목이 파일명에 없음 — 카카오페이지 다운로드가 만드는
    이름이고, 예전 도구로 받은 파일은 뒤에 #페이지수가 붙어 있다)를 인식한다. 맞으면
    (회차 번호 문자열, 부제목, 파일명에 적힌 페이지수 — 없으면 None)를 반환하고, 아니면 None.
    페이지수가 파일명에 있으면 zip을 열어보지 않고도 알 수 있어서, 원본이 rclone이라 zip 내용에 접근 못 하는 경우에도
    템플릿을 적용할 수 있다. 없으면(새 이름) {page_count}가 필요한 템플릿은 zip을 열어 세고, 열 수 없으면 적용하지 않는다."""
    match = _KAKAO_STYLE_FILENAME_RE.match(stem)
    if not match:
        return None
    episode_no, subtitle, page_count = match.group(1), match.group(2), match.group(3)
    return episode_no, subtitle, int(page_count) if page_count is not None else None


def _list_episode_files_sorted(title_dir: Path) -> list[tuple[int, Path]]:
    """제목 폴더 바로 밑의 zip 파일들을, 파일명 맨 앞 번호 기준으로 오름차순 정렬해서 반환한다."""
    if not title_dir.is_dir():
        return []
    results = []
    try:
        entries = list(title_dir.iterdir())
    except OSError as e:
        log.error("다운로드 폴더 목록 조회 실패 (%s): %s", title_dir, e)
        return []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".zip":
            continue
        match = _LEADING_DIGITS_RE.match(entry.name)
        if match:
            results.append((int(match.group(1)), entry))
    results.sort(key=lambda pair: pair[0])
    return results


def _count_zip_pages(zip_path: Path) -> int | None:
    """zip 안에 든 이미지(페이지) 개수를 실제로 세서 반환한다. zip을 못 열면(손상 등)
    None을 반환한다 — 이 경우 {page_count} 토큰이 있는 템플릿은 적용하지 않는다."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return sum(1 for n in zf.namelist() if not n.endswith("/"))
    except (zipfile.BadZipFile, OSError) as e:
        log.warning("zip 페이지 수 계산 실패 (%s): %s", zip_path, e)
        return None


def render_archive_filename(
    template: str, filename: str, title_name: str, writer_names: list[str],
    zip_path_for_page_count: Path | None = None,
) -> str | None:
    """템플릿에 맞춰 최종 파일명(확장자 포함)을 만든다. 두 가지 파일명 구조를 순서대로
    시도한다: ① 네이버식 '{번호} {제목} {부제목}' ② 카카오식(제목 없이 받는 다른
    도구 등) '{번호}_{부제목}#{페이지수}' — 이 경우 페이지수는 파일명에 이미 있어서
    zip을 안 열어도 된다. 둘 다 안 맞으면 None을 반환해서 원본 파일명을 그대로 쓰게 한다.

    {page_count} 토큰을 쓰는데 ①번 구조라서 zip을 직접 열어서 세야 하는 경우,
    zip_path_for_page_count(실제 로컬 경로)가 없으면(원격이라 접근 못 하는 경우 등)
    역시 None을 반환한다."""
    if not template.strip():
        return None
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    parsed = _parse_zip_filename_for_template(stem, title_name)
    embedded_page_count = None
    if parsed is not None:
        episode_no, subtitle = parsed
    else:
        kakao_parsed = _parse_kakao_style_filename(stem)
        if kakao_parsed is None:
            return None
        episode_no, subtitle, embedded_page_count = kakao_parsed

    rendered = template
    if "{page_count}" in rendered:
        if embedded_page_count is not None:
            page_count = embedded_page_count
        elif zip_path_for_page_count is not None:
            page_count = _count_zip_pages(zip_path_for_page_count)
            if page_count is None:
                return None
        else:
            return None  # 페이지수가 필요한데 구할 방법이 없음(원격이라 zip을 못 엶)
        rendered = rendered.replace("{page_count}", str(page_count))
    rendered = rendered.replace("{title}", title_name)
    rendered = rendered.replace("{episode_no}", episode_no)
    rendered = rendered.replace("{subtitle}", subtitle)
    rendered = rendered.replace("{author}", ", ".join(writer_names))

    rendered = remove_forbidden_str(rendered)
    if not rendered:
        return None
    return rendered + suffix


def preview_filename_for_title(download_root: str, title_name: str, template: str, writer_names: list[str], kakao: bool = False) -> dict:
    """설정 화면의 미리보기용 — 실제 다운로드 폴더에 있는 회차 zip 파일 하나를 골라서,
    템플릿을 적용하면 실제로 어떤 이름이 되는지 보여준다. 가장 번호가 큰(최근) 파일을
    보여준다 — 사용자가 알아보기 쉬운 최신 회차일 가능성이 높아서."""
    title_dir = _title_folder(download_root, title_name, kakao)
    files = _list_episode_files_sorted(title_dir)
    if not files:
        return {"original_filename": None, "rendered_filename": None, "message": "다운로드 폴더에 zip 파일이 없습니다."}
    _num, src = files[-1]
    if not template.strip():
        return {"original_filename": src.name, "rendered_filename": None, "message": "템플릿이 비어있어 원본 파일명 그대로 이동됩니다."}
    rendered = render_archive_filename(template, src.name, title_name, writer_names, zip_path_for_page_count=src)
    if rendered is None:
        return {
            "original_filename": src.name,
            "rendered_filename": None,
            "message": "이 파일명 구조를 인식하지 못해 원본 파일명 그대로 이동됩니다.",
        }
    return {"original_filename": src.name, "rendered_filename": rendered, "message": "정상적으로 변환됩니다."}


def preview_filename_for_folder(
    local_root: str, rclone_config_path: str, source_dest_type: str, source_path: str, template: str
) -> dict:
    """임의의 폴더(로컬이면 local_root 밑, 등록된 대상 여부와 무관) 기준 미리보기.
    local_root는 호출부가 정해서 넘긴다(ARCHIVE_ROOT 또는 DOWNLOAD_ROOT일 수 있음 —
    폴더 일괄 이동이 두 루트를 다 지원하므로).
    원본이 로컬이면 웹툰 미리보기와 동일하게 실제 템플릿 적용 결과를 보여준다.
    원본이 rclone이면, 파일명 자체에 페이지수가 있는 구조(카카오식)라면 zip을
    안 열어도 되니 그대로 적용하고, 페이지수를 zip에서 직접 세야 하는 구조(네이버식
    + {page_count})라면 그 사실을 그대로 안내한다 — 실제 아카이빙 동작과 다른
    미리보기를 보여주지 않기 위함."""
    display_name = _derive_folder_display_name(source_dest_type, source_path)

    if source_dest_type == "rclone":
        try:
            remote, path = _parse_rclone_target(source_path)
            names = rclone_client.list_top_level_files(rclone_config_path, remote, path)
        except Exception as e:
            return {"original_filename": None, "rendered_filename": None, "message": f"원본 원격 폴더 목록을 읽을 수 없습니다: {e}"}
        zip_names = [n for n in names if n.endswith(".zip") and _LEADING_DIGITS_RE.match(n)]
        if not zip_names:
            return {"original_filename": None, "rendered_filename": None, "message": "이 폴더에 zip 파일이 없습니다."}
        zip_names.sort(key=lambda n: int(_LEADING_DIGITS_RE.match(n).group(1)))
        original = zip_names[-1]
        if not template.strip():
            return {"original_filename": original, "rendered_filename": None, "message": "템플릿이 비어있어 원본 파일명 그대로 이동됩니다."}
        rendered = render_archive_filename(template, original, display_name, [])
        if rendered is None:
            return {
                "original_filename": original,
                "rendered_filename": None,
                "message": "이 파일명 구조를 인식하지 못했거나(또는 {page_count}를 쓰려면 zip을 직접 열어야 하는데 "
                "원격이라 못 열어서), 원본 파일명 그대로 이동됩니다.",
            }
        return {"original_filename": original, "rendered_filename": rendered, "message": "정상적으로 변환됩니다."}

    src_dir = _local_archive_path(local_root, source_path)
    files = _list_episode_files_sorted(src_dir)
    if not files:
        return {"original_filename": None, "rendered_filename": None, "message": "이 폴더에 zip 파일이 없습니다."}
    _num, src = files[-1]
    if not template.strip():
        return {"original_filename": src.name, "rendered_filename": None, "message": "템플릿이 비어있어 원본 파일명 그대로 이동됩니다."}
    rendered = render_archive_filename(template, src.name, display_name, [], zip_path_for_page_count=src)
    if rendered is None:
        return {
            "original_filename": src.name,
            "rendered_filename": None,
            "message": "이 파일명 구조를 인식하지 못해 원본 파일명 그대로 이동됩니다.",
        }
    return {"original_filename": src.name, "rendered_filename": rendered, "message": "정상적으로 변환됩니다."}
