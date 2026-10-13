"""주기 작업 정의 + 진행상황 로깅 + 잡별 스케줄(끄기/N분마다/특정 요일·시각) 관리.

두 가지 독립적인 작업으로 나눈다:
  - discovery_job   : 완결 감지(+감지 즉시 디스코드 봇으로 확인 메시지 전송) + 작가/태그 신작 자동추가
  - download_job    : 구독 중인 웹툰의 새 회차 다운로드 (회차 하나마다 압축까지 끝내고 다음 화로 진행)

(예전에는 디스코드 완결-확인 스레드를 폴링하는 commands_job이 따로 있었지만,
discord_bot.py의 실시간 Gateway 봇으로 대체되어 더 이상 필요 없다 — 폴링 자체가 없어짐.)

각 잡은 웹툰 하나 처리 중 예외가 나도 다른 웹툰 처리를 막지 않도록 individually try/except.
각 잡의 스케줄은 DB(settings 테이블)에 사용자가 저장한 값이 있으면 그걸 쓰고, 없으면
기본값(신작 스캔 6시간마다 / 다운로드 1시간마다, 전부 interval 모드)을 쓴다 —
설정 페이지에서 바꾸면 reschedule_all()로 즉시 반영된다.
"""

from app.scheduler.common import (
    DEFAULT_SCHEDULES,
)
from app.scheduler.naver_download import (
    _download_new_episodes_for_one,
    _download_pending_batches,
)
from app.scheduler.kakao_download import (
    _KAKAO_MAX_BATCHES_PER_TITLE,
    _download_kakao_subscriptions,
    _kakao_remaining_free,
    _notify_kakao_caught_up,
    _notify_kakao_rental_used,
    _notify_kakao_ticket_needed,
)
from app.scheduler.download_job import (
    _run_download_job_impl,
    run_download_job,
)
from app.scheduler.archive_job import (
    run_archive_job,
)
from app.scheduler.report_job import (
    _SETTING_KEY_REPORT_LAST_SENT_AT,
    _build_report_message,
    _collect_kakao_new_episodes,
    _collect_unregistered_new_episodes,
    _run_report_job_impl,
    run_report_job,
)
from app.scheduler.discovery_job import (
    _notify_newly_finished,
    _run_discovery_job_impl,
    run_discovery_job,
    run_kakao_catalog_refresh_job,
)
from app.scheduler.schedule import (
    MISFIRE_GRACE_SECONDS,
    create_scheduler,
    reschedule_all,
)

__all__ = [
    "DEFAULT_SCHEDULES",
    "MISFIRE_GRACE_SECONDS",
    "_KAKAO_MAX_BATCHES_PER_TITLE",
    "_SETTING_KEY_REPORT_LAST_SENT_AT",
    "_build_report_message",
    "_collect_kakao_new_episodes",
    "_collect_unregistered_new_episodes",
    "_download_kakao_subscriptions",
    "_download_new_episodes_for_one",
    "_download_pending_batches",
    "_kakao_remaining_free",
    "_notify_kakao_caught_up",
    "_notify_kakao_rental_used",
    "_notify_kakao_ticket_needed",
    "_notify_newly_finished",
    "_run_discovery_job_impl",
    "_run_download_job_impl",
    "_run_report_job_impl",
    "create_scheduler",
    "reschedule_all",
    "run_archive_job",
    "run_discovery_job",
    "run_download_job",
    "run_kakao_catalog_refresh_job",
    "run_report_job",
]
