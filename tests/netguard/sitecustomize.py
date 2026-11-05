"""테스트 프로세스 전용 — 외부 네트워크 접속을 항상 즉시 실패시킨다(tests/run_backend.py가 PYTHONPATH에 이 폴더를 넣어서 켠다. 앱 실행에는 쓰이지 않는다).
개발 샌드박스처럼 네트워크가 막힌 곳과 CI처럼 열린 곳에서 테스트 결과가 달라지는 것을 막는다: 테스트가 가짜로 바꾸지 않은 외부 접속은 어디서든 같은 오류로 실패한다.
루프백(127.x, ::1, localhost)과 유닉스 소켓은 허용한다."""
import socket

_MESSAGE = "테스트에서는 외부 네트워크를 쓰지 않는다 — 외부 호출은 테스트에서 가짜로 바꿔 주세요"
_LOCAL_NAMES = {"localhost", "test", "testserver", ""}


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if host is None:
        return True
    host = str(host).strip("[]").lower()
    return host in _LOCAL_NAMES or host.startswith("127.") or host in ("::1", "0.0.0.0", "::")


def _block(host) -> None:
    raise OSError(f"{_MESSAGE}: {host}")


_original_getaddrinfo = socket.getaddrinfo
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_local(host):
        _block(host)
    return _original_getaddrinfo(host, *args, **kwargs)


def _target_host(address):
    return address[0] if isinstance(address, tuple) and address else None   # 문자열 주소(유닉스 소켓)는 None → 허용


def _guarded_connect(self, address):
    host = _target_host(address)
    if host is not None and not _is_local(host):
        _block(host)
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    host = _target_host(address)
    if host is not None and not _is_local(host):
        _block(host)
    return _original_connect_ex(self, address)


socket.getaddrinfo = _guarded_getaddrinfo
socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
