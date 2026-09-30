"""
시리즈 루트 폴더에 ComicInfo.xml과 커버 이미지를 생성한다.

필드는 기존에 쓰던 info.xml 스키마(ComicInfo)를 유지하되, 네이버 API로
실제 확보 가능한 값만 채운다. ViewCount/LikeCount/CommentUrl처럼 네이버
article/list/info 응답에 없는 필드(예시 파일은 카카오웹툰 기준)는 빈 태그로 둔다.
"""

import logging
from pathlib import Path
from xml.sax.saxutils import escape

import aiohttp

from app.constants import DEFAULT_HEADERS, NAVER_SERIES_URL_TEMPLATE
from app.file_utils import guess_image_extension
from app.models import TitleInfo

log = logging.getLogger(__name__)

_COMICINFO_TEMPLATE = """<?xml version="1.0"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>{title}</Title>
  <Series>{title}</Series>
  <Summary>{summary}</Summary>
  <Writer>{writer}</Writer>
  <Publisher>{publisher}</Publisher>
  <Genre>{genre}</Genre>
  <Tags>{tags}</Tags>
  <LanguageISO>ko</LanguageISO>
  <Notes>{notes}</Notes>
  <CoverArtist>{cover_artist}</CoverArtist>
  <Penciller></Penciller>
  <Inker></Inker>
  <Colorist></Colorist>
  <Letterer></Letterer>
  <Editor></Editor>
  <Characters></Characters>
  <Web>{web}</Web>
  <CommunityRating></CommunityRating>
  <AgeRating>{age_rating}</AgeRating>
  <Count></Count>
  <Manga>No</Manga>
  <SeriesStatus>{series_status}</SeriesStatus>
  <FreeCount></FreeCount>
  <ViewCount></ViewCount>
  <CommentCount></CommentCount>
  <LikeCount></LikeCount>
  <CommentUrl></CommentUrl>
</ComicInfo>
"""

# 네이버/카카오가 같은 등급을 서로 다른 이름("전체연령가" vs "전체이용가")으로
# 부르는 경우가 있어서, info.xml에서는 하나로 통일한다. 여기 없는 값(15세 이용가
# 등)은 원래 문구를 그대로 쓴다.
_AGE_RATING_NORMALIZE = {
    "전체연령가": "전체이용가",
}


def _normalize_age_rating(age_description: str) -> str:
    return _AGE_RATING_NORMALIZE.get(age_description, age_description)


def render_comicinfo(
    *, title: str, summary: str, writer: str, cover_artist: str, notes: str, genre: str, tags: str,
    publisher: str, web: str, age_rating: str, series_status: str,
) -> str:
    """네이버/카카오 공통 — 값을 XML로 이스케이프해서 같은 템플릿에 채운다."""
    return _COMICINFO_TEMPLATE.format(
        title=escape(title), summary=escape(summary), writer=escape(writer), cover_artist=escape(cover_artist),
        notes=escape(notes), genre=escape(genre), tags=escape(tags), publisher=escape(publisher), web=escape(web),
        age_rating=escape(age_rating), series_status=series_status,
    )


def kakao_age_rating(age_grade: int | str | None) -> str:
    """카카오페이지 연령 등급(age_grade: 0/15/19)을 info.xml 표기로 — 15세 이용가, 18세 이용가, 그 외는 전부 전체이용가.
    응답에서는 정수지만, 타입이 달라지거나 값이 이상해도 예외로 info.xml 작성이 통째로 실패하지 않게 숫자로 바꿔 본다."""
    try:
        grade = int(str(age_grade).strip())
    except ValueError:
        return "전체이용가"
    if grade >= 18:
        return "18세 이용가"
    if grade == 15:
        return "15세 이용가"
    return "전체이용가"


def build_kakao_comicinfo_xml(series_item: dict, series_id: int, about: dict | None = None) -> str:
    """카카오페이지 작품 정보로 info.xml을 만든다. 작품 "정보" 탭(about)을 받았으면 글은 Writer, 그림은 CoverArtist,
    원작은 Notes("원작: ...")에 나눠 적고 테마 키워드는 Tags에 적는다. 못 받았으면 목록 응답의 작가 이름 전부를
    Writer에 적는다(역할을 알 수 없어서)."""
    groups: dict[str, list[str]] = {"writer": [], "illustrator": [], "original_author": []}
    tags: list[str] = []
    if about:
        for author in about.get("author_list") or []:
            names = groups.get(author.get("role"))
            if names is not None and author.get("name") and author["name"] not in names:
                names.append(author["name"])
        tags = [t["title"] for t in about.get("theme_keyword_list") or [] if t.get("title")]
    writers, illustrators, originals = groups["writer"], groups["illustrator"], groups["original_author"]
    if not (writers or illustrators or originals):
        writers = [a.strip() for a in (series_item.get("authors") or "").split(",") if a.strip()]
    return render_comicinfo(
        title=series_item.get("title", ""), summary=series_item.get("description") or "", writer=", ".join(writers),
        cover_artist=", ".join(illustrators), notes=f"원작: {', '.join(originals)}" if originals else "",
        genre=series_item.get("sub_category") or "", tags=",".join(tags), publisher="카카오페이지",
        web=f"https://page.kakao.com/content/{series_id}", age_rating=kakao_age_rating(series_item.get("age_grade")),
        series_status="완결" if series_item.get("on_issue") == "N" else "연재",
    )


def build_comicinfo_xml(info: TitleInfo) -> str:
    writer_names = ", ".join(dict.fromkeys(info.writer_names))
    cover_artist_names = ", ".join(dict.fromkeys(info.painter_names))
    notes = f"원작: {', '.join(dict.fromkeys(info.novel_origin_names))}" if info.novel_origin_names else ""
    # genres_ko가 비어있으면(과거에 만들어진 캐시 등) genres(원본 코드)로라도 대체한다 —
    # 항상 뭐라도 나오는 게, 아무것도 안 나오는 것보다 낫다.
    genre_display = info.genres_ko or info.genres
    return render_comicinfo(
        title=info.title_name, summary=info.synopsis, writer=writer_names, cover_artist=cover_artist_names, notes=notes,
        genre=",".join(genre_display), tags=",".join(info.tags), publisher="네이버웹툰",
        web=NAVER_SERIES_URL_TEMPLATE.format(title_id=info.title_id),
        age_rating=_normalize_age_rating(info.age_description), series_status="완결" if info.is_finished else "연재",
    )


def needs_comicinfo(webtoon_dir: Path) -> bool:
    """커버 이미지가 없으면 True (다운로드 비용이 있어서 없을 때만 다시 받음).
    info.xml은 여기 포함하지 않는다 — 작은 텍스트 파일이라 매번 새로 쓰는 비용이
    거의 없어서, 있든 없든 항상 최신 정보로 덮어쓰기 때문이다(write_comicinfo_file
    호출부에서 이 함수와 별개로 매번 호출됨). 예전엔 정보가 부실하게(예: 작가 이름이
    비어있게) 한 번 생성되면 그 상태로 영영 굳어버리는 문제가 있었는데, 그걸 막기
    위한 설계 변경이다."""
    if not webtoon_dir.is_dir():
        return True
    return not any(webtoon_dir.glob("cover.*"))


def write_comicinfo_file(webtoon_dir: Path, info: TitleInfo) -> None:
    webtoon_dir.mkdir(parents=True, exist_ok=True)
    xml_content = build_comicinfo_xml(info)
    (webtoon_dir / "info.xml").write_text(xml_content, encoding="utf-8")


async def download_cover_image(
    session: aiohttp.ClientSession, webtoon_dir: Path, info: TitleInfo, timeout_seconds: int
) -> None:
    if not info.thumbnail_url:
        return
    try:
        async with session.get(
            info.thumbnail_url,
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            if response.status != 200:
                log.warning("커버 이미지 다운로드 실패 (titleId=%s): HTTP %s", info.title_id, response.status)
                return
            ext = guess_image_extension(info.thumbnail_url)
            webtoon_dir.mkdir(parents=True, exist_ok=True)
            new_cover = webtoon_dir / f"cover{ext}"
            new_cover.write_bytes(await response.read())
            for old in webtoon_dir.glob("cover.*"):  # 확장자가 다른 옛 커버가 남아 cover.*가 둘 이상 되지 않게(새 커버를 저장한 뒤에만 지운다)
                if old != new_cover:
                    old.unlink(missing_ok=True)
    except Exception as e:
        log.warning("커버 이미지 다운로드 중 오류 (titleId=%s): %s", info.title_id, e)
