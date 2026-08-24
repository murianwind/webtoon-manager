"""
사용자가 바꿀 이유가 없는 고정 상수.
설정(config.py)과 구분: 여긴 "네이버/디스코드 API 자체의 주소"처럼 서비스 스펙에
고정된 값만 둔다. 튜닝 가능한 값(배치 크기, 타임아웃 등)은 config.py 쪽.
"""

NAVER_API_BASE = "https://comic.naver.com/api"
NAVER_INFO_URL = f"{NAVER_API_BASE}/article/list/info"
NAVER_LIST_URL = f"{NAVER_API_BASE}/article/list"
NAVER_ARTIST_OTHER_TITLES_URL = f"{NAVER_API_BASE}/artist/otherTitle/list"
NAVER_CURATION_LIST_URL = f"{NAVER_API_BASE}/curation/list"
NAVER_CURATION_META_URL = f"{NAVER_API_BASE}/curation/meta"
NAVER_WEEKDAY_LIST_URL = f"{NAVER_API_BASE}/webtoon/titlelist/weekday"
NAVER_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

NAVER_DETAIL_URL_TEMPLATES = {
    "webtoon": "https://comic.naver.com/webtoon/detail",
    "challenge": "https://comic.naver.com/challenge/detail",
    "bestChallenge": "https://comic.naver.com/bestChallenge/detail",
}

NAVER_SERIES_URL_TEMPLATE = "https://comic.naver.com/webtoon/list?titleId={title_id}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://comic.naver.com/webtoon/list",
}

DISCORD_API = "https://discord.com/api/v10"
DISCORD_MESSAGE_CHUNK_LIMIT = 1900

# 웹툰 폴더 네이밍 규칙 (기존 NWebtoon_Downloader / change.py와 동일)
FORBIDDEN_CHAR_TABLE_FROM = '\\/:*?"<>|..'
FORBIDDEN_CHAR_TABLE_TO = "￦／：＊？＂˂˃｜․․"

# 다운로드 재시도 정책
IMAGE_DOWNLOAD_MAX_RETRIES = 5
EPISODE_LIST_MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 1.0
