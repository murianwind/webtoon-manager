"""
"도움말" 탭 — README(일반 사용자용 설명서)를 화면에 보여줄 HTML로 바꾼다.

- 코드 블록/표/번호 목록(번호 이어짐)/접기(<details>)를 지원하고, 맨 위에 섹션 목차를 붙인다.
- README는 Docker 이미지에 들어 있어서, 새 이미지가 배포되면(깃헙 → 이미지 빌드 → 자동 업데이트) 도움말도 같이 바뀐다.
- 한글 제목도 그대로 이동 링크(#카카오페이지-자세히)가 되도록 한글 유지 슬러그를 쓴다.
"""

import html

import markdown
from markdown.extensions.toc import slugify_unicode

# fenced_code는 번호 목록 안의 코드 블록을 처리하지 못하므로, README에서 코드 블록은 목록 밖에 둔다(test_docs가 검사).
_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "md_in_html", "toc"]
_CONFIGS = {"toc": {"slugify": slugify_unicode, "toc_depth": "2-3"}}


def _toc_html(tokens: list[dict]) -> str:
    """제목(h2/h3)으로 이동하는 목차. 중첩은 h2 아래 h3까지만."""
    if not tokens:
        return ""

    def items(nodes: list[dict]) -> str:
        parts = []
        for node in nodes:
            children = items(node["children"]) if node["children"] else ""
            parts.append(f'<li><a href="#{html.escape(node["id"])}">{html.escape(node["name"])}</a>{children}</li>')
        return "<ul>" + "".join(parts) + "</ul>"

    # h1(문서 제목)은 목차에서 빼고 그 아래 제목들만 보여준다
    top = tokens[0]["children"] if len(tokens) == 1 and tokens[0]["level"] == 1 else tokens
    return f'<nav class="help-toc"><strong>목차</strong>{items(top)}</nav>'


def render_help(text: str) -> str:
    """README 마크다운 → 도움말 HTML(목차 + 본문). README는 이 저장소가 직접 관리하는 문서라 HTML 태그(<details> 등)를 그대로 허용한다."""
    md = markdown.Markdown(extensions=_EXTENSIONS, extension_configs=_CONFIGS)
    body = md.convert(text)
    return _toc_html(md.toc_tokens) + body
