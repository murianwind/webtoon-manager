"""로그 마스킹 — 디스코드 웹훅/봇 토큰 + 카카오페이지의 서명 주소(token/signature/credential)와 로그인 쿠키 값."""
import json, logging, time
from app import db, discord_config, kakao_page_auth as auth
db.get_connection()
from app import log_redaction as lr

def rec(msg, *args):
    return logging.LogRecord("t", logging.WARNING, "f", 1, msg, args, None)

# ── 1. 서명 주소의 비밀 쿼리 값 ──
url = "https://dw-img-page.kakao.com/sdownload/resource?token=eTd3c_Kiz-N%2BPF%3D%3D&filename=o1&x=1"
out = lr.redact(f"이미지 실패 {url}")
assert "eTd3c_Kiz" not in out and "token=***" in out and "filename=o1" in out and "x=1" in out            # 비밀 값만 가리고 나머지는 그대로
out = lr.redact("https://page-edge.kakao.com/sdownload/resource?kid=abc&signature=ZZZ9&expires=1790&credential=CRED123&filename=o1")
assert "ZZZ9" not in out and "CRED123" not in out and "signature=***" in out and "credential=***" in out and "kid=abc" in out and "expires=1790" in out
assert lr.redact("token=abc") == "token=***" and lr.redact("Token=abc&Signature=d") == "Token=***&Signature=***"                                  # 대소문자
assert lr.redact("mytoken=abc 그리고 signature 값은 없음") == "mytoken=abc 그리고 signature 값은 없음"                                              # 다른 이름의 매개변수/일반 문장은 건드리지 않는다
print("1) 서명 주소 마스킹 OK")

# ── 2. 저장된 카카오 로그인 쿠키 값 ──
auth.save_cookies(auth.parse_cookie_export(json.dumps([{"domain": ".kakao.com", "name": n, "value": f"secretvalue-{n}-0123456789", "path": "/", "expirationDate": time.time() + 9e5} for n in auth.REQUIRED_COOKIES])))
name = auth.REQUIRED_COOKIES[0]
out = lr.redact(f"요청 실패: Cookie: {name}=secretvalue-{name}-0123456789; 나머지")
assert f"secretvalue-{name}" not in out and "***" in out and "나머지" in out
assert lr.redact("짧은 값 v 는 가리지 않는다") == "짧은 값 v 는 가리지 않는다"                                                                       # 값이 너무 짧으면(오탐 방지) 일반 문장은 그대로
print("2) 저장된 쿠키 값 마스킹 OK")

# ── 3. 디스코드 웹훅/봇 토큰(기존 동작 유지) ──
# 실제 저장 함수를 쓴다(DB에는 암호화돼 저장되고 get_*가 복호화해서 돌려준다)
discord_config.set_webhook_url("https://discord.com/api/webhooks/123/SECRETHOOK"); discord_config.set_bot_token("BOT-TOKEN-XYZ-987654")
out = lr.redact("실패 https://discord.com/api/webhooks/123/SECRETHOOK 그리고 BOT-TOKEN-XYZ-987654")
assert "SECRETHOOK" not in out and "BOT-TOKEN-XYZ" not in out and out.count("***REDACTED***") == 2, out
print("3) 디스코드 비밀 마스킹 OK")

# ── 4. 로깅 필터: %s 인자까지 반영, 비밀 조회가 실패해도 로그는 계속 나간다 ──
f = lr.RedactingFilter(); r = rec("이미지 실패: %s (%d번)", url, 3)
assert f.filter(r) is True and "eTd3c_Kiz" not in r.getMessage() and "3번" in r.getMessage()
orig = lr._secret_values
lr._secret_values = lambda: (_ for _ in ()).throw(RuntimeError("DB 오류"))
r = rec("일반 로그 token=abc"); assert f.filter(r) is True and "token=***" in r.getMessage()                          # 비밀 목록을 못 읽어도 주소 마스킹은 하고 로그는 막지 않는다
lr._secret_values = orig
r = rec("평범한 로그"); f.filter(r); assert r.getMessage() == "평범한 로그"
print("4) 로깅 필터 OK (인자 반영, 조회 실패 격리)")
print("\n전부 통과")
