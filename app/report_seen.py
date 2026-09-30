"""
다운로드 리포트의 "새 에피소드" 섹션(등록 안 한 작품의 UP 표시)에서, 이미 알린 회차를 기억해서 다시 알리지 않는다.

UP 표시는 새 회차가 나온 뒤 한동안 그대로 남아 있어서, 하루에 리포트를 여러 번 받으면 같은 작품이 매번 나왔다.
이제 작품마다 "마지막으로 알린 회차"를 기억하고, 그 회차와 같으면 건너뛴다(더 새로운 회차가 나오면 다시 알린다).

- 네이버: 식별값 = 최신 회차 번호 / 카카오: 식별값 = 최신 회차 주소(구독 여부가 바뀌어도 같은 회차면 반복하지 않음)
- 전송이 성공한 뒤에만 기록한다(호출부 책임) — 실패하면 다음 리포트에서 다시 시도된다.
- 기억하는 작품 수는 MAX_ENTRIES로 제한한다(오래된 것부터 잊음).
"""

import json

from app import repository

SETTING_KEY = "report_seen_new_episodes"
MAX_ENTRIES = 5000

NaverItem = tuple[str, str, int]  # (title_id, title_name, 최신 회차 번호)
KakaoItem = tuple[int, str, str, bool]  # (title_id, title_name, 바로가기 URL, 구독 중 여부)


def _load() -> dict[str, str]:
    try:
        data = json.loads(repository.get_setting(SETTING_KEY) or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}  # 깨진 값은 없는 셈 치고 다시 쌓는다(한 번 더 알리는 것이 못 알리는 것보다 낫다)


def _naver_key(item: NaverItem) -> tuple[str, str]:
    return f"naver:{item[0]}", str(item[2])


def _kakao_key(item: KakaoItem) -> tuple[str, str]:
    return f"kakao:{item[0]}", item[2]


def filter_unseen(naver_items: list[NaverItem], kakao_items: list[KakaoItem]) -> tuple[list[NaverItem], list[KakaoItem]]:
    """이미 같은 회차로 알린 작품을 뺀 목록."""
    seen = _load()
    return (
        [i for i in naver_items if seen.get(_naver_key(i)[0]) != _naver_key(i)[1]],
        [i for i in kakao_items if seen.get(_kakao_key(i)[0]) != _kakao_key(i)[1]],
    )


def remember(naver_items: list[NaverItem], kakao_items: list[KakaoItem]) -> None:
    """방금 알린 작품들의 회차를 기록한다."""
    if not naver_items and not kakao_items:
        return
    seen = _load()
    for key, identity in [*map(_naver_key, naver_items), *map(_kakao_key, kakao_items)]:
        seen.pop(key, None)  # 다시 넣어서 "가장 최근에 알린" 위치로 옮긴다(크기 제한 때 오래된 것부터 잊기 위해)
        seen[key] = identity
    while len(seen) > MAX_ENTRIES:
        seen.pop(next(iter(seen)))
    repository.set_setting(SETTING_KEY, json.dumps(seen, ensure_ascii=False))
