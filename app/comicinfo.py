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
  <Publisher>네이버웹툰</Publisher>
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


def build_comicinfo_xml(info: TitleInfo) -> str:
    writer_names = ", ".join(dict.fromkeys(info.writer_names))
    cover_artist_names = ", ".join(dict.fromkeys(info.painter_names))
    notes = f"원작: {', '.join(dict.fromkeys(info.novel_origin_names))}" if info.novel_origin_names else ""
    # genres_ko가 비어있으면(과거에 만들어진 캐시 등) genres(원본 코드)로라도 대체한다 —
    # 항상 뭐라도 나오는 게, 아무것도 안 나오는 것보다 낫다.
    genre_display = info.genres_ko or info.genres
    return _COMICINFO_TEMPLATE.format(
        title=escape(info.title_name),
        summary=escape(info.synopsis),
        writer=escape(writer_names),
        cover_artist=escape(cover_artist_names),
        notes=escape(notes),
        genre=escape(",".join(genre_display)),
        tags=escape(",".join(info.tags)),
        web=escape(NAVER_SERIES_URL_TEMPLATE.format(title_id=info.title_id)),
        age_rating=escape(info.age_description),
        series_status="완결" if info.is_finished else "연재",
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
            (webtoon_dir / f"cover{ext}").write_bytes(await response.read())
    except Exception as e:
        log.warning("커버 이미지 다운로드 중 오류 (titleId=%s): %s", info.title_id, e)
