"""구독 실행 이력 — 웹툰을 구독할 때마다(수동 구독, 카카오 신작 스캔 자동 구독) 구독했다는 사실과 작가/태그 관리에 올린 결과를
실행 이력("subscription") 한 건으로 남긴다. 네이버/카카오가 같은 문구로 남기도록 문구 만들기는 여기에 모은다."""

from app import job_status, kakao_authors, repository

JOB_NAME = "subscription"

_STATE_LABEL = {
    repository.AUTHOR_ADDED: "신규",
    repository.AUTHOR_ALREADY_ON: "이미 등록됨",
    repository.AUTHOR_ALREADY_OFF: "꺼져 있어 켜지 않음",
}


def describe_registration(role_label: str, results: list[tuple[str, str]], enabled: bool) -> str:
    """작가 등록 결과 한 줄. results는 (이름, upsert_watched_author가 돌려준 상태). enabled=False면 켜지 않고 전체 작가 목록에만 올린 경우다."""
    if not enabled:
        return f"작가 자동 등록 설정이 꺼져 있어 켜지 않고 전체 작가 목록에만 올림: {', '.join(name for name, _ in results)}"
    return f"{role_label} 등록: " + ", ".join(f"{name}({_STATE_LABEL[state]})" for name, state in results)


def record(platform: str, title: str, details: list[str], success: bool = True, action: str = "구독") -> None:
    """"[플랫폼] 제목 — 구독" 줄과 그 뒤 세부 줄들을 실행 이력 한 건으로 남긴다."""
    prefix = f"[{platform}] {title} — "
    job_status.record_event(JOB_NAME, [prefix + action] + [prefix + d for d in details], success)


def record_kakao_subscription(title: str, about: dict | None, auto_register_enabled: bool, action: str = "구독") -> None:
    """카카오 작품 하나의 구독 결과를 남기고, 설정이 켜져 있으면 작품 정보의 작가(원작자가 있으면 원작자, 없으면 글 작가)를 관심 작가로 등록한다.
    작품 정보를 못 받았으면 등록하지 않고 이력을 실패(error)로 남긴다."""
    if not auto_register_enabled:
        record("카카오", title, ["작가 자동 등록 설정이 꺼져 있어 등록하지 않음"], action=action)
        return
    if about is None:
        record("카카오", title, ["작품 정보 조회 실패 — 작가 등록을 건너뜀"], success=False, action=action)
        return
    names = kakao_authors.authors_to_register(about)
    if not names:
        record("카카오", title, ["작가 정보 없음 (글/원작 작가가 없음)"], action=action)
        return
    results = [(name, repository.upsert_watched_author(name, name, True, "kakao")) for name in names]
    role_label = "원작자" if kakao_authors.split_authors(about)[2] else "작가"
    record("카카오", title, [describe_registration(role_label, results, True)], action=action)
