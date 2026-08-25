"""
모듈 간 주고받는 데이터 구조. 여기 정의된 것 외에 raw dict를 여기저기로
넘기지 않도록 해서(=DRY/가독성) API 응답 파싱은 naver_api.py 한 곳에만 몰아둔다.
"""

from dataclasses import dataclass, field


@dataclass
class EpisodeInfo:
    episode_no: int
    subtitle: str
    is_locked: bool  # thumbnailLock — 유료/미공개로 잠긴 회차


@dataclass
class TitleInfo:
    title_id: str
    title_name: str
    synopsis: str
    is_adult: bool
    webtoon_type: str  # "webtoon" | "challenge" | "bestChallenge"
    is_finished: bool
    thumbnail_url: str
    writer_names: list[str] = field(default_factory=list)
    painter_names: list[str] = field(default_factory=list)
    writer_ids: set[str] = field(default_factory=set)
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    age_description: str = ""


@dataclass
class NaverListItem:
    """네이버 '요일별 전체 웹툰' 목록의 한 항목 (구독 여부와 무관하게 존재하는 원본 목록)."""

    title_id: str
    title_name: str
    thumbnail_url: str
    weekdays: list[str] = field(default_factory=list)
    is_finished: bool = False
    author_summary: str = ""


@dataclass
class WebtoonRecord:
    """SQLite webtoons 테이블 한 행에 대응."""

    title_id: str
    title: str
    status: str  # active | unsubscribed | excluded
    is_adult: bool
    writer_ids: list[str]
    added_source: str  # manual | artist | tag
    last_downloaded_no: int
    is_finished: bool
    finish_ack: bool
    thumbnail_url: str = ""
    finish_notified: bool = False
