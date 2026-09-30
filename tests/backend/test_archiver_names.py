"""아카이버: 카카오페이지 새 파일명(페이지 수 없음)과 예전 이름(#페이지수)을 둘 다 인식한다."""
import tempfile, zipfile
from pathlib import Path
from app import archiver as a

p = a._parse_kakao_style_filename
assert p("0384_384화 복국 (2)#48") == ("0384", "384화 복국 (2)", 48)          # 예전 이름
assert p("0384_384화 복국 (2)") == ("0384", "384화 복국 (2)", None)            # 새 이름
assert p("0002_1화#4") == ("0002", "1화", 4) and p("0001_프롤로그") == ("0001", "프롤로그", None)
assert p("0012_제3화_ 시작？") == ("0012", "제3화_ 시작？", None) and p("0005_5화 #2 특별편") == ("0005", "5화 #2 특별편", None)   # 부제목 안의 #는 끝이 숫자가 아니면 그대로
assert p("제목_001화_부제") is None and p("abc") is None
d = Path(tempfile.mkdtemp()); z = d / "0384_384화 복국 (2).zip"
with zipfile.ZipFile(z, "w") as zf:
    for i in range(3): zf.writestr(f"{i}.jpg", b"x")
r = a.render_archive_filename
assert r("{episode_no}화 {subtitle}", z.name, "복국", ["작가"]) == "0384화 384화 복국 (2).zip"
assert r("{title} {episode_no} ({page_count}p)", z.name, "복국", ["작가"], zip_path_for_page_count=z) == "복국 0384 (3p).zip"     # 페이지 수는 zip을 열어서 센다
assert r("{title} {episode_no} ({page_count}p)", z.name, "복국", ["작가"]) is None                                                 # 세지 못하면(원격) 적용하지 않는다
assert r("{title} {episode_no} ({page_count}p)", "0384_384화 복국 (2)#48.zip", "복국", ["작가"]) == "복국 0384 (48p).zip"           # 예전 이름은 파일명의 값을 그대로
print("아카이버 파일명 인식 OK"); print("\n전부 통과")
