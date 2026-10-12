"""수동 다운로드의 "표지·정보 받기" — 분석한 웹툰의 cover와 info.xml만 zip 하나로 브라우저에 내려주는 공용 도우미(네이버/카카오페이지).

서버 폴더에는 아무것도 쓰지 않는다 — info.xml/표지를 만드는 기존 함수가 파일로 쓰기 전에 만들어 내는 내용(바이트)을 그대로 zip에 담아 응답으로 보낸다.
"""
import io
import urllib.parse
import zipfile
from dataclasses import dataclass, field

COVER_MISSING = "cover-missing"  # 표지를 못 받아 info.xml만 담았다는 표시(응답 헤더 X-Metadata-Warning 값 — 헤더라서 ASCII 코드로 둔다)


@dataclass
class MetadataBundle:
    title: str  # 작품 이름(zip 파일 이름에 쓴다)
    files: dict[str, bytes]  # zip 안의 파일 이름 → 내용 (info.xml, cover.jpg 등)
    warnings: list[str] = field(default_factory=list)


def build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def attachment_headers(filename: str, warnings: list[str] | None = None) -> dict[str, str]:
    """브라우저가 파일로 저장하게 하는 헤더. 한글 이름은 RFC 5987(filename*=UTF-8'')로 보내고, 옛 브라우저용 ASCII 이름도 함께 둔다."""
    headers = {"Content-Disposition": f"attachment; filename=\"metadata.zip\"; filename*=UTF-8''{urllib.parse.quote(filename)}"}
    if warnings:
        headers["X-Metadata-Warning"] = ",".join(warnings)
    return headers
