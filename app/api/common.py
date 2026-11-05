"""여러 라우터가 함께 쓰는 모델/헬퍼."""


import html
import json
import logging

from fastapi import Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from app import metadata_zip
from app import (
    repository,
)


log = logging.getLogger(__name__)



def _is_author_auto_register_enabled() -> bool:
    """구독 시 그 작품 작가를 '등록된 작가'(자동 신작추가 대상)로 자동 등록할지 여부(저장소의 같은 기준을 쓴다)."""
    return repository.is_author_auto_register_enabled()


def _js_json(value) -> str:
    """스크립트 블록 안에 넣을 JSON 값 — 작품명(주소의 title 값 등)에 </script>나 따옴표가 들어 있어도 블록을 빠져나가 실행되지 않게
    <, >, &를 \\u 이스케이프로 바꾼다(JSON으로 만든 문자열이라 따옴표/역슬래시는 이미 안전하다)."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# 제외 확인 페이지(확인 화면/이미 제외 안내)가 같이 쓰는 스타일 — 디스코드 앱에서 눌러 모바일 브라우저로 열리므로 폭을 꽉 채우는 큰 버튼과 어두운 화면 지원
_EXCLUDE_PAGE_STYLE = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 24px 16px 40px; font-family: -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif; background: #f4f5f7; color: #1c1c1e; line-height: 1.6; }
.card { max-width: 480px; margin: 0 auto; padding: 28px 20px; background: #fff; border-radius: 16px; box-shadow: 0 2px 14px rgba(0, 0, 0, 0.08); text-align: center; }
h1 { margin: 0 0 16px; font-size: 22px; line-height: 1.4; font-weight: 700; overflow-wrap: anywhere; }
.question { margin: 0 0 6px; font-size: 18px; }
.note { margin: 0 0 8px; font-size: 14px; color: #6b6b70; }
.btn { display: block; width: 100%; box-sizing: border-box; min-height: 56px; margin-top: 16px; padding: 14px 20px; font-family: inherit; font-size: 18px; font-weight: 600; text-align: center; text-decoration: none; border: 1px solid transparent; border-radius: 12px; cursor: pointer; }
.btn:active { opacity: 0.8; }
.btn:disabled { opacity: 0.6; cursor: default; }
.btn-primary { background: #1c1c1e; color: #fff; }
.btn-link { background: #03c75a; color: #fff; }
.btn-secondary { background: transparent; color: inherit; border-color: #c7c7cc; }
.done { margin: 0 0 4px; font-size: 22px; font-weight: 700; color: #1a7f37; }
#error-text { margin: 12px 0 0; min-height: 1.6em; font-size: 16px; color: #b00020; }
@media (prefers-color-scheme: dark) {
  body { background: #121214; color: #f2f2f7; }
  .card { background: #1c1c1e; box-shadow: none; }
  .note { color: #a1a1a6; }
  .btn-primary { background: #f2f2f7; color: #111; }
  .btn-secondary { border-color: #48484a; }
  .done { color: #3fb950; }
  #error-text { color: #ff6b6b; }
}
"""


def _render_exclude_confirm_html(title_id, title: str, exclude_endpoint: str, payload: dict, already_excluded: bool = False) -> HTMLResponse:
    """다운로드 리포트의 '목록 제외' 링크가 여는 페이지 — 네이버/카카오 공용.
    디스코드는 링크를 메시지에 올리면 미리보기(임베드)를 만들려고 그 URL을 자동으로
    한 번 열어보는데, 그 GET 요청만으로 실제 제외가 일어나면 사용자가 누르지도
    않았는데 제외되는 사고가 난다 — 그래서 GET은 아무것도 바꾸지 않고 확인 버튼이
    있는 페이지만 보여주고, 실제 제외는 그 페이지 안에서 버튼을 눌러야 exclude_endpoint로
    별도 POST가 나가게 분리했다.

    주로 디스코드 앱에서 눌러 모바일 브라우저로 열린다 — viewport 메타가 없으면 데스크톱 폭으로 그려서 글자/버튼이 아주 작게 보이므로,
    화면 폭을 꽉 채우는 큰 버튼(높이 56px)과 큰 글자로 만들고 어두운 화면 설정에서도 보이게 했다."""
    safe_title = html.escape(title or str(title_id))
    if already_excluded:
        # 이미 제외된 작품 — 제외하기 버튼 없이 안내만(여기서는 아무것도 바꾸지 않는다)
        return HTMLResponse(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>목록에서 제외</title>
<style>
{_EXCLUDE_PAGE_STYLE}
</style></head>
<body>
<div class="card">
<h1>{safe_title}</h1>
<p class="done">이미 목록에서 제외된 웹툰입니다.</p>
<p class="note">제외됨 탭에서 언제든 되돌릴 수 있습니다.</p>
<a class="btn btn-link" href="/">서비스로 이동</a>
</div>
</body></html>""")
    payload_json = _js_json(payload)
    endpoint_json = _js_json(exclude_endpoint)
    return HTMLResponse(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>목록에서 제외</title>
<style>
{_EXCLUDE_PAGE_STYLE}
</style></head>
<body>
<div class="card">
<h1>{safe_title}</h1>
<p id="question" class="question">이 작품을 "제외됨" 목록으로 옮길까요?</p>
<p id="note" class="note">제외됨 탭에서 언제든 되돌릴 수 있습니다.</p>
<button id="btn" class="btn btn-primary">제외하기</button>
<p id="error-text"></p>
<div id="result"></div>
</div>
<script>
document.getElementById("btn").addEventListener("click", async () => {{
  const btn = document.getElementById("btn");
  const errorText = document.getElementById("error-text");
  btn.disabled = true;
  btn.textContent = "처리 중...";
  errorText.textContent = "";
  try {{
    const res = await fetch({endpoint_json}, {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({payload_json}),
    }});
    if (res.ok) {{
      document.getElementById("question").style.display = "none";
      document.getElementById("note").style.display = "none";
      btn.style.display = "none"; // 버튼은 없애고, 완료됐다는 게 한눈에 보이게 결과만 남긴다
      document.getElementById("result").innerHTML =
        '<p class="done">✅ 제외되었습니다.</p>' +
        '<a class="btn btn-link" href="/">서비스로 이동</a>' +
        '<button id="close-btn" class="btn btn-secondary">이 탭 닫기</button>';
      const closeBtn = document.getElementById("close-btn");
      closeBtn.addEventListener("click", () => {{
        window.close();
        // 이 페이지를 직접 눌러서 열었으면(스크립트가 새 창으로 연 게 아니면)
        // 브라우저 보안 정책상 window.close()가 조용히 안 먹힐 수 있다 —
        // 그런 경우를 위한 안내만 남겨둔다.
        errorText.style.color = "#6b6b70";
        errorText.textContent = "탭이 자동으로 안 닫히면 직접 닫아주세요.";
      }});
    }} else {{
      const err = await res.json().catch(() => ({{}}));
      errorText.textContent = "실패: " + (err.detail || res.status);
      btn.disabled = false;
      btn.textContent = "제외하기";
    }}
  }} catch (e) {{
    errorText.textContent = "오류: " + e.message;
    btn.disabled = false;
    btn.textContent = "제외하기";
  }}
}});
</script>
</body></html>""")


class RetentionDaysOut(BaseModel):
    retention_days: int  # 0 = 자동삭제 끔


class RetentionDaysIn(BaseModel):
    retention_days: int

    @field_validator("retention_days")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("보관 기간은 0 이상이어야 합니다 (0 = 자동삭제 끔).")
        return v


def metadata_zip_response(bundle: "metadata_zip.MetadataBundle", safe_title: str) -> Response:
    """"표지·정보 받기" 응답 — cover/info.xml이 담긴 zip을 브라우저 다운로드로 내려준다(파일 이름은 '<작품 이름>_표지정보.zip')."""
    return Response(
        content=metadata_zip.build_zip(bundle.files),
        media_type="application/zip",
        headers=metadata_zip.attachment_headers(f"{safe_title}_표지정보.zip", bundle.warnings),
    )

