"""자동 다운로드의 속도 조절 — 작품당 한 번에 받을 회차 상한과, 상한에 걸렸을 때 이어받기 전에 쉬는 시간.

기본값은 환경변수(Settings.max_new_episodes_per_title=10, batch_rest_minutes=5)이고, 설정 화면에서 바꾼 값(DB settings)이 있으면 그 값을 쓴다.
읽을 때마다 DB를 보므로 재시작 없이 다음 배치부터 바로 적용된다. DB에 범위 밖이거나 숫자가 아닌 값이 들어 있으면 그 항목만 기본값으로 돌아간다 —
이상한 값으로 다운로드가 돌지 않게 하려는 것이다(기본값 자체는 검사하지 않는다).
"""
from app import repository

CAP_KEY = "download_max_new_episodes_per_title"
REST_KEY = "download_batch_rest_minutes"
CAP_MIN, CAP_MAX = 0, 200  # 0은 "제한 없음"(받을 회차를 한 번에 다 받고 쉬지 않음)
REST_MIN, REST_MAX = 0.5, 120.0  # 분 — 너무 짧으면 쉬는 의미가 없어서 30초 아래는 막는다


def validate(cap: int, rest_minutes: float) -> str | None:
    """저장 전 입력 검사 — 맞으면 None, 아니면 사람이 읽을 이유."""
    if not CAP_MIN <= cap <= CAP_MAX:
        return f"작품당 한 번에 받을 회차는 {CAP_MIN}~{CAP_MAX} 사이여야 합니다(0은 제한 없음)."
    if not REST_MIN <= rest_minutes <= REST_MAX:
        return f"쉬는 시간은 {REST_MIN:g}~{REST_MAX:g}분 사이여야 합니다."
    return None


def _saved_cap() -> int | None:
    try:
        value = int(repository.get_setting(CAP_KEY) or "")
    except ValueError:
        return None
    return value if CAP_MIN <= value <= CAP_MAX else None


def _saved_rest() -> float | None:
    try:
        value = float(repository.get_setting(REST_KEY) or "")
    except ValueError:
        return None
    return value if REST_MIN <= value <= REST_MAX else None


def limits(settings) -> tuple[int, float]:
    """(작품당 한 번에 받을 회차 상한(0=제한 없음), 배치 사이 쉬는 시간(분)) — 설정 화면에서 저장한 값이 있으면 그 값, 없으면 환경변수 기본값."""
    cap, rest = _saved_cap(), _saved_rest()
    return (settings.max_new_episodes_per_title if cap is None else cap, settings.batch_rest_minutes if rest is None else rest)


def is_customized() -> bool:
    return _saved_cap() is not None or _saved_rest() is not None


def save(cap: int, rest_minutes: float) -> None:
    repository.set_setting(CAP_KEY, str(cap))
    repository.set_setting(REST_KEY, f"{rest_minutes:g}")


def reset() -> None:
    repository.set_setting(CAP_KEY, None)
    repository.set_setting(REST_KEY, None)
