"""웹툰 아카이빙: 다운로드 폴더(A)에 쌓인 회차 zip을 사용자가 지정한 별도 폴더(B, ARCHIVE_ROOT
기준)로 옮긴다. 세 가지 트리거가 있다 — 주기적 이동(마지막 파일 보존), 수동 이동(동일 규칙),
완결 구독해제 시 자동 전체이동(마지막 파일 예외 없음). 셋 다 목적지 계산(resolve_archive_dest)과
실제 이동(move_file_with_conflict_policy)이라는 같은 두 함수를 거치므로 로직이 갈라지지 않는다.

핵심 설계 결정(전부 사용자와 논의 확정):
- "마지막 파일"은 파일명 맨 앞 번호 기준 — zipper.py의 _LEADING_DIGITS_RE를 그대로 재사용해서
  번호 파싱 로직이 두 곳에서 어긋나지 않게 한다.
- 목적지 base_path는 여러 웹툰이 공유할 수 있는 "그릇" 폴더 — 실제 저장 위치는
  {base_path}/{원본 폴더명} 서브폴더가 되는데, 이건 그 base_path를 "지정해서" 쓰는
  활성(enabled) 웹툰이 2개 이상일 때만 그렇다. 1개면 base_path 바로 밑에 쌓인다.
  완결 자동이동의 기본 경로(archive_default_base_path)는 항상 여러 웹툰을 받는 공용
  그릇이 목적이므로 예외 없이 항상 서브폴더를 만든다.
- 이미 파일이 하나라도 있는 폴더를 목적지로 지정하려 하면 경고를 준다(선택 API에서
  검증) — 나중에 다른 웹툰이 같은 폴더를 고르면서 파일이 뒤섞이는 걸 막기 위한
  주의 장치일 뿐, 강제로 막지는 않는다(예전엔 여기서 막았었지만, 사용자 요청으로
  경고 후 진행 가능하도록 완화됨).
"""

from app.archiver.settings import (
    _DEFAULT_BASE_PATH_SETTING_KEY,
    _get_effective_filename_template,
    get_conflict_policy,
    get_default_base_path,
    get_default_dest_type,
    get_filename_template,
    is_finish_unsubscribe_archiving_enabled,
)
from app.archiver.naming import (
    preview_filename_for_folder,
    preview_filename_for_title,
    render_archive_filename,
)
from app.archiver.transfer import (
    _derive_folder_display_name,
    _move_episode_file,
    is_folder_selectable_as_dest,
    is_folder_selectable_as_dest_rclone,
    move_file_with_conflict_policy,
    resolve_archive_dest,
)
from app.archiver.title_archive import (
    KAKAO_TARGET_PREFIX,
    WebtoonSource,
    _archive_title,
    manual_archive_now,
    process_pending_finish_archives,
    queue_finish_archive_if_applicable,
    resolve_webtoon_source,
    run_periodic_archive,
)
from app.archiver.folder_archive import (
    _archive_folder_target,
    _bulk_move_single_file,
    _download_kakao_cover_to_temp_file,
    _generic_copy_file,
    bulk_move_folder,
)

__all__ = [
    "KAKAO_TARGET_PREFIX",
    "WebtoonSource",
    "_DEFAULT_BASE_PATH_SETTING_KEY",
    "_archive_folder_target",
    "_archive_title",
    "_bulk_move_single_file",
    "_derive_folder_display_name",
    "_download_kakao_cover_to_temp_file",
    "_generic_copy_file",
    "_get_effective_filename_template",
    "_move_episode_file",
    "bulk_move_folder",
    "get_conflict_policy",
    "get_default_base_path",
    "get_default_dest_type",
    "get_filename_template",
    "is_finish_unsubscribe_archiving_enabled",
    "is_folder_selectable_as_dest",
    "is_folder_selectable_as_dest_rclone",
    "manual_archive_now",
    "move_file_with_conflict_policy",
    "preview_filename_for_folder",
    "preview_filename_for_title",
    "process_pending_finish_archives",
    "queue_finish_archive_if_applicable",
    "render_archive_filename",
    "resolve_archive_dest",
    "resolve_webtoon_source",
    "run_periodic_archive",
]
