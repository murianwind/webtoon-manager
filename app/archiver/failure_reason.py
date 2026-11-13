"""아카이빙 이동 실패의 오류 문구(rclone이 남기는 여러 줄짜리 로그 전문 등)를 사람이 한눈에 볼 **한 줄 사유**로 줄인다 — 디스코드 알림에는 이 사유만 나가고(같은 사유는 묶어서),
자세한 오류 전문은 실행 로그(수동 실행 화면의 아카이빙 로그, 설정의 실행 이력)에 그대로 남는다. 위에서부터 처음 맞는 것을 쓴다(순서가 중요: 이 앱이 만든 문구 → 인증/권한 → 서버 오류 →
저장 공간 → 네트워크 → 시간 초과; 504 "Gateway Timeout"은 시간 초과가 아니라 서버 오류로 본다). 모르는 오류는 첫 줄을 짧게 잘라서 쓴다."""
import re

UNKNOWN = "알 수 없는 오류"
_MAX_LENGTH = 60

# (정규식, 사유) — 사유는 한 줄·60자 이하. 같은 사유는 같은 문구여야 알림에서 묶인다
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"읽음 상태"), "뷰어 읽음 상태를 확인하지 못해 건너뜀"),
    (re.compile(r"0KB"), "원본 파일이 0KB(다운로드가 깨졌을 수 있음)"),
    (re.compile(r"올라간 파일"), "업로드 확인 실패(올라간 크기 불일치) — 원본은 그대로"),
    (re.compile(r"\b401\b|unauthori[sz]ed|invalid_grant|AADSTS", re.I), "보관 위치 접근 거절(401) — 서비스 장애나 로그인 만료 가능"),
    (re.compile(r"\b403\b|forbidden|access\s*denied", re.I), "보관 위치 접근 거부(403) — 권한을 확인하세요"),
    (re.compile(r"\b429\b|too many requests|throttl", re.I), "요청이 너무 많음(429) — 잠시 후 다시 시도"),
    (re.compile(r"\b50[234]\b|service unavailable|bad gateway|gateway time-?out", re.I), "보관 위치 서버 일시 오류(5xx) — 잠시 후 다시 시도"),
    (re.compile(r"\b507\b|insufficient storage|no space left|quota", re.I), "저장 공간 부족"),
    (re.compile(r"no such host|connection (refused|reset)|network is unreachable|dial tcp", re.I), "네트워크 연결 오류"),
    (re.compile(r"time-?d? ?out|deadline exceeded|안에 끝나지 않았습니다", re.I), "연결 시간 초과"),
]


def summarize(error: str | None) -> str:
    """오류 문구 → 한 줄 사유(60자 이하). 비어 있으면 "알 수 없는 오류"."""
    text = (error or "").strip()
    for pattern, reason in _RULES:
        if pattern.search(text):
            return reason
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), UNKNOWN)
    return first_line if len(first_line) <= _MAX_LENGTH else first_line[: _MAX_LENGTH - 1] + "…"
