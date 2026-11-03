"""웹툰 목록의 요일 표기 통일과 네이버 요일 지도 저장.

화면(전체목록/제외됨의 요일별 탭)은 모든 목록 응답에서 같은 형식의 요일(mon~sun, 월~일 순서)을 기대한다:
- 네이버: 요일별 목록 API의 MONDAY~SUNDAY를 바꾼다(모르는 값은 무시).
- 카카오: 요일 탭(1~7 = 월~일)마다 나온 작품에 그 요일을 붙인다(kakao_api).
제외됨/구독해제 탭은 네이버를 부르지 않고 DB 기록으로 목록을 만들어서 요일을 모른다 — 전체목록을 불러올 때(네이버 목록을 어차피 받는다) 요일을 설정 테이블에
{작품 번호: [요일]}로 저장해 두고 그 탭들이 거기서 채운다. 요일별 목록에 없는 작품(완결/장기 휴재)은 요일이 없다 — 화면에서 "기타"에 모인다.
"""
import json

from app import repository

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NAVER_MAP_KEY = "naver_weekday_map"
_NAVER_DAYS = {"MONDAY": "mon", "TUESDAY": "tue", "WEDNESDAY": "wed", "THURSDAY": "thu", "FRIDAY": "fri", "SATURDAY": "sat", "SUNDAY": "sun"}


def from_naver(codes) -> list[str]:
    """네이버의 MONDAY~SUNDAY를 mon~sun으로(월~일 순서, 중복/모르는 값 제외)."""
    found = {_NAVER_DAYS[code] for code in codes or [] if code in _NAVER_DAYS}
    return [day for day in DAYS if day in found]


def get_naver_map() -> dict[str, list[str]]:
    raw = repository.get_setting(NAVER_MAP_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_naver_map(days_by_id: dict[str, list[str]]) -> None:
    """네이버 요일별 목록에서 받은 {작품 번호: [요일]}을 저장한다(요일이 있는 작품만). 바뀐 게 없으면 쓰지 않는다 — 목록을 열 때마다 큰 값을 다시 쓰지 않게."""
    wanted = {title_id: days for title_id, days in days_by_id.items() if days}
    if wanted != get_naver_map():
        repository.set_setting(NAVER_MAP_KEY, json.dumps(wanted, ensure_ascii=False, sort_keys=True))
