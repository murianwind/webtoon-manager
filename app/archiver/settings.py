"""아카이빙 설정 값(충돌 정책, 기본 경로/대상 종류, 파일 이름 틀, 완결 시 구독해제 후 아카이빙).
"""

from app import repository

_CONFLICT_POLICY_SETTING_KEY = "archive_conflict_policy"


_DEFAULT_BASE_PATH_SETTING_KEY = "archive_default_base_path"


_DEFAULT_DEST_TYPE_SETTING_KEY = "archive_default_dest_type"


_ON_FINISH_UNSUBSCRIBE_SETTING_KEY = "archive_on_finish_unsubscribe"


_FILENAME_TEMPLATE_SETTING_KEY = "archive_filename_template"


def get_conflict_policy() -> str:
    return repository.get_setting(_CONFLICT_POLICY_SETTING_KEY) or "skip"


def get_default_base_path() -> str | None:
    return repository.get_setting(_DEFAULT_BASE_PATH_SETTING_KEY)


def get_default_dest_type() -> str:
    return repository.get_setting(_DEFAULT_DEST_TYPE_SETTING_KEY) or "local"


def get_filename_template() -> str:
    """빈 문자열이면 파일명 템플릿 미사용(원본 zip 파일명 그대로 이동)을 뜻한다."""
    return repository.get_setting(_FILENAME_TEMPLATE_SETTING_KEY) or ""


def _get_effective_filename_template(preset_id: int | None) -> str:
    """대상에 프리셋이 지정돼 있으면 그 프리셋의 템플릿을, 없으면(None) 전역
    기본값을 쓴다. 지정된 프리셋이 삭제되어 더는 없으면 전역 기본값으로 조용히
    대체한다(대상 등록이 갑자기 깨지는 것보다 안전)."""
    if preset_id is not None:
        preset = repository.get_filename_template_preset(preset_id)
        if preset is not None:
            return preset.template
    return get_filename_template()


def is_finish_unsubscribe_archiving_enabled() -> bool:
    return repository.get_setting(_ON_FINISH_UNSUBSCRIBE_SETTING_KEY) == "1"
