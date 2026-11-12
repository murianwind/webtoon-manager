"""실행 중인 아카이빙의 중단 요청 — 이동은 별도 스레드에서 도는 동기 코드라 도중에 강제로 끊지 않고, 파일 하나가 끝날 때마다 이 요청을 확인해서 멈춘다
(지금 옮기는 파일은 복사가 끝나고 원본이 지워진 뒤에 멈추므로 반쯤 옮겨진 파일이 남지 않는다). 정기 실행, "지금 이동", 완결 구독해제 대기열, 폴더 대상이 같은 요청을 본다.
요청은 실행을 시작할 때 비운다(reset) — 지난 요청이 남아서 새 실행이 아무것도 못 하는 일이 없게. 스레드 사이에서 쓰므로 threading.Event다."""
import threading

_event = threading.Event()


def request() -> None:
    _event.set()


def reset() -> None:
    _event.clear()


def is_requested() -> bool:
    return _event.is_set()
