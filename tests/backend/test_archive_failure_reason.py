"""아카이빙 이동 실패의 오류 문구(rclone 로그 전문 등)를 사람이 한눈에 볼 한 줄 사유로 줄인다 — 디스코드 알림에는 이 사유만 나가고, 자세한 오류는 실행 로그에 남는다(app/archiver/failure_reason.py).
실제로 받은 알림의 오류 문구(OneDrive 401, SharePoint 503 HTML 본문 등)와 이 앱이 만드는 문구(원본 0KB, 크기 불일치, 읽음 상태 확인 실패)를 그대로 쓴다."""
from app.archiver.failure_reason import UNKNOWN, summarize

RCLONE_401 = '''2026/11/13 08:18:34 NOTICE: 0015_15화 (3).zip: Failed to cancel multipart upload: HTTP error 401 (401 Unauthorized) returned body: "" (upload failed due to: HTTP error 401 (401 Unauthorized) returned body: "")
2026/11/13 08:18:34 ERROR : 0015_15화.zip: Failed to copy: HTTP error 401 (401 Unauthorized) returned body: ""
2026/11/13 08:18:34 ERROR : Attempt 1/3 failed with 1 errors and: HTTP error 401 (401 Unauthorized) returned body: ""'''
RCLONE_503 = 'low level retry 1/10 (error HTTP error 503 (503 Service Unavailable) returned body: "<!DOCTYPE html><html><head><title>Sharepoint Online</title></head><body>Something went wrong. This is a temporary issue</body></html>")'

CASES = [
    (RCLONE_401, "401"),                                                          # 실제 알림: 업로드 거절(인증) — 서비스 장애나 로그인 만료
    ("HTTP error 403 (403 Forbidden) returned body: \"\"", "403"),
    ('couldn\'t list directory: accessDenied: Access denied', "접근"),
    (RCLONE_503, "서버 일시"),
    ("HTTP error 502 (502 Bad Gateway)", "서버 일시"),
    ("HTTP error 504 (504 Gateway Timeout) returned body: \"\"", "서버 일시"),                  # 504는 시간 초과 단어가 들어 있어도 서버 오류로 본다
    ("HTTP error 429 (429 Too Many Requests) returned body: \"\"", "429"),
    ("rclone 명령이 1800초 안에 끝나지 않았습니다 (timeout)", "시간 초과"),
    ("context deadline exceeded", "시간 초과"),
    ("dial tcp: lookup graph.microsoft.com: no such host", "네트워크"),
    ("read tcp 172.18.0.2:5000->13.107.136.9:443: connection reset by peer", "네트워크"),
    ("HTTP error 507 (507 Insufficient Storage)", "저장 공간"),
    ("write /data/x.zip: no space left on device", "저장 공간"),
    ("원본 파일이 0KB입니다(다운로드가 깨졌을 수 있습니다): 0015_15화.zip — 옮기지 않고 원본을 그대로 둡니다", "0KB"),
    ("올라간 파일의 크기가 원본과 다릅니다(원격 없음, 원본 100바이트): a.zip — 원본을 그대로 둡니다", "업로드 확인"),
    ("올라간 파일을 확인하지 못했습니다: 목록 실패 — 원본을 그대로 둡니다", "업로드 확인"),
    ("뷰어의 읽음 상태를 확인하지 못해 옮기지 않았습니다(다음 실행에서 다시 확인합니다)", "읽음 상태"),
]
for error, expected in CASES:
    reason = summarize(error)
    assert expected in reason, f"{expected!r}가 사유에 없다: {reason!r} ← {error[:80]!r}"
    assert "\n" not in reason and len(reason) <= 60, f"사유는 한 줄·60자 이하: {reason!r}"
    assert "2026/11/13" not in reason and "Attempt" not in reason and "returned body" not in reason, f"오류 문구가 그대로 새어 나왔다: {reason!r}"
print("1) 알려진 오류 → 한 줄 사유 OK")

# 401은 "인증 오류"만이 아니라 서비스 장애일 수도 있다는 단서를 붙여서 사용자가 앱을 의심하지 않게 한다
assert "장애" in summarize(RCLONE_401) or "로그인" in summarize(RCLONE_401), summarize(RCLONE_401)
# 같은 사유는 같은 문구(그래야 묶인다) — 다른 파일 이름/시각이 달라도
assert summarize(RCLONE_401) == summarize(RCLONE_401.replace("0015_15화", "0099_99화").replace("08:18:34", "09:00:00"))
print("2) 같은 사유는 같은 문구 OK")

# 모르는 오류는 첫 줄(공백만인 줄 제외)을 짧게 자른 것 — 한 줄·60자 이하. 비어 있으면 알 수 없음
assert summarize("처음 보는 오류가 났습니다") == "처음 보는 오류가 났습니다"
assert summarize("\n\n  첫 줄입니다  \n둘째 줄") == "첫 줄입니다"
long_unknown = summarize("가" * 300 + "\n다음 줄"); assert len(long_unknown) <= 60 and long_unknown.endswith("…"), long_unknown
for empty in ("", "   ", None): assert summarize(empty) == UNKNOWN, repr(empty)
print("3) 모르는 오류/빈 값 OK")
print("ALL OK")
