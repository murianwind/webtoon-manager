"""여러 라우터가 함께 쓰는 모델/헬퍼."""


import html
import json
import logging

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from app import (
    repository,
)


log = logging.getLogger(__name__)



def _is_author_auto_register_enabled() -> bool:
    """구독 시 그 작품 작가를 '등록된 작가'(자동 신작추가 대상)로 자동 등록할지 여부.
    값이 명시적으로 '0'일 때만 꺼짐 — 기존 사용자는 값이 아예 없을 테니 켜짐 유지."""
    return repository.get_setting("auto_register_author_on_subscribe") != "0"


def _render_exclude_confirm_html(title_id, title: str, exclude_endpoint: str, payload: dict) -> HTMLResponse:
    """다운로드 리포트의 '목록 제외' 링크가 여는 페이지 — 네이버/카카오 공용.
    디스코드는 링크를 메시지에 올리면 미리보기(임베드)를 만들려고 그 URL을 자동으로
    한 번 열어보는데, 그 GET 요청만으로 실제 제외가 일어나면 사용자가 누르지도
    않았는데 제외되는 사고가 난다 — 그래서 GET은 아무것도 바꾸지 않고 확인 버튼이
    있는 페이지만 보여주고, 실제 제외는 그 페이지 안에서 버튼을 눌러야 exclude_endpoint로
    별도 POST가 나가게 분리했다."""
    safe_title = html.escape(title or str(title_id))
    payload_json = json.dumps(payload)
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>목록에서 제외</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 420px; margin: 60px auto; text-align: center; padding: 0 20px; color: #222; }}
button {{ padding: 10px 22px; font-size: 15px; margin-top: 10px; cursor: pointer; border: 1px solid #ccc; border-radius: 6px; background: #f5f5f5; }}
button:disabled {{ opacity: 0.6; cursor: default; }}
a.btn-link {{ display: inline-block; padding: 10px 22px; font-size: 15px; margin-top: 10px; border: 1px solid #ccc; border-radius: 6px; background: #f5f5f5; color: #222; text-decoration: none; }}
#result {{ margin-top: 20px; }}
#result p {{ font-size: 15px; color: #1a7f37; margin: 0 0 4px; }}
#error-text {{ font-size: 14px; color: #b00020; margin-top: 10px; }}
</style></head>
<body>
<h3>{safe_title}</h3>
<p id="question">이 작품을 "제외됨" 목록으로 옮길까요? (제외됨 탭에서 다시 되돌릴 수 있습니다)</p>
<button id="btn">제외하기</button>
<p id="error-text"></p>
<div id="result"></div>
<script>
document.getElementById("btn").addEventListener("click", async () => {{
  const btn = document.getElementById("btn");
  const errorText = document.getElementById("error-text");
  btn.disabled = true;
  btn.textContent = "처리 중...";
  errorText.textContent = "";
  try {{
    const res = await fetch("{exclude_endpoint}", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({payload_json}),
    }});
    if (res.ok) {{
      document.getElementById("question").style.display = "none";
      btn.style.display = "none"; // 버튼은 없애고, 완료됐다는 게 한눈에 보이게 결과만 남긴다
      document.getElementById("result").innerHTML =
        '<p>✅ 제외되었습니다.</p>' +
        '<a class="btn-link" href="/">서비스로 이동</a> ' +
        '<button id="close-btn">이 탭 닫기</button>';
      const closeBtn = document.getElementById("close-btn");
      closeBtn.addEventListener("click", () => {{
        window.close();
        // 이 페이지를 직접 눌러서 열었으면(스크립트가 새 창으로 연 게 아니면)
        // 브라우저 보안 정책상 window.close()가 조용히 안 먹힐 수 있다 —
        // 그런 경우를 위한 안내만 남겨둔다.
        errorText.style.color = "#555";
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
