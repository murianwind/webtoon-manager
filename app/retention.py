"""이력 보관 기간 정리 — 실행 이력/다운로드 이력/아카이빙 이력이 같은 규칙을 쓴다.

설정(일수)이 없거나 0 이하이거나 숫자가 아니면 아무것도 지우지 않는다. 양수면 그 일수보다 오래된 이력을 지우고, 지운 게 있으면 해당 작업 로그에 한 줄 남긴다
(받은 파일이나 옮겨진 파일은 그대로 — 이력 기록만 정리한다).
"""
from typing import Callable

from app import job_status, repository


def retention_days(setting_key: str) -> int | None:
    """설정에 저장된 보관 일수. 없거나 0 이하이거나 숫자가 아니면 None(정리 안 함)."""
    raw = (repository.get_setting(setting_key) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def cleanup_history(job_name: str, setting_key: str, delete_older_than: Callable[[int], int], label: str) -> int:
    """보관 기간이 설정돼 있으면 그보다 오래된 이력을 지우고(delete_older_than(일수) → 지운 개수), 지운 게 있으면 job_name 작업 로그에 남긴다. 지운 개수를 돌려준다."""
    days = retention_days(setting_key)
    if days is None:
        return 0
    deleted = delete_older_than(days)
    if deleted:
        job_status.log_line(job_name, f"보관기간({days}일) 초과 {label} {deleted}건 정리")
    return deleted
