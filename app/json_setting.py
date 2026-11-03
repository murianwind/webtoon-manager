"""설정 테이블의 한 항목에 JSON 한 덩어리로 저장하는 상태의 공용 도우미.

리포트 발송 상태(report_dispatch), 뷰어 스캔 상태(viewer_scan), 요일/카카오 상태 조회 결과(weekdays, kakao_status)가 모두 "읽고 → 바꾸고 → 쓰기"로
같은 항목의 JSON을 갱신한다. 여러 스레드(asyncio.to_thread, 작업 종료 알림 등)에서 동시에 갱신하면 뒤에 쓴 쪽이 앞의 변경을 지워 버리므로(실제로
GitHub Actions에서 걸렸다) 이 도우미가 한 항목마다 잠금으로 읽고-바꾸고-쓰기를 한 덩어리로 보호한다. 없거나 깨진 값은 기본값으로 다룬다.
"""
import json
import threading
from typing import Callable

from app import repository


class JsonSetting:
    def __init__(self, key: str, defaults: Callable[[], dict] = dict) -> None:
        """key: 설정 키. defaults: 기본값을 만들어 돌려주는 함수(매번 새로 만들어서 돌려받은 것을 바꿔도 기본값이 오염되지 않는다). 맵으로 쓸 때는 기본(빈 dict)."""
        self.key = key
        self._defaults = defaults
        self.lock = threading.RLock()  # 같은 스레드에서 update/edit 안에서 다시 load/update를 불러도 되게 RLock

    def load(self) -> dict:
        raw = repository.get_setting(self.key)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return {**self._defaults(), **data} if isinstance(data, dict) else self._defaults()

    def save(self, data: dict) -> None:
        repository.set_setting(self.key, json.dumps(data, ensure_ascii=False))

    def update(self, **fields) -> dict:
        """이 필드들만 바꾸고 나머지는 그대로 둔 뒤 저장하고, 바뀐 전체를 돌려준다."""
        with self.lock:
            data = {**self.load(), **fields}
            self.save(data)
            return data

    def edit(self, change: Callable[[dict], None]) -> dict:
        """change(data)가 data를 직접 바꾸면 저장하고 돌려준다 — 맵에 항목을 넣거나 빼는 것처럼 한 필드 갱신으로 안 되는 변경용."""
        with self.lock:
            data = self.load()
            change(data)
            self.save(data)
            return data

    def clear(self) -> None:
        repository.set_setting(self.key, None)
