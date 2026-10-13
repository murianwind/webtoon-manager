"""저장소 공통 — 상태/출처 상수와 현재 시각.
"""

from datetime import datetime, timezone

STATUS_ACTIVE = "active"


STATUS_UNSUBSCRIBED = "unsubscribed"


STATUS_EXCLUDED = "excluded"


STATUS_UNREGISTERED = "unregistered"  # 한 번이라도 구독했던 작품을 "목록으로" 보낼 때만 쓰는 상태.


# 구독해제/제외됨 어느 탭에도 안 뜨고(그 탭들은 unsubscribed/excluded만 조회하므로),
# 네이버 전체목록 보완 로직(구독/제외됨과 무관하게 DB에 남아있으면 계속 보여줌)에서는
# excluded만 걸러내고 이 상태는 안 걸러내므로, 완결/휴재라 요일별 목록엔 없는 작품도
# 전체목록에서 계속 찾을 수 있다 — 구독한 적 없는 작품은 완전 삭제해버리므로 이 상태를
# 아예 거치지 않는다(그런 건 네이버 목록에서 사라지면 같이 사라져도 상관없다고 확인함).

SOURCE_MANUAL = "manual"


SOURCE_ARTIST = "artist"


SOURCE_TAG = "tag"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
