"""테스트 프로세스에서는 외부 네트워크가 항상 즉시 실패한다 — 개발 샌드박스(네트워크 막힘)와 CI(네트워크 열림)가 같은 조건에서 돌게 하려는 장치(tests/netguard/sitecustomize.py, run_backend.py가 켠다).
예전에는 테스트가 가짜로 바꾸지 않은 외부 접속이 샌드박스에서는 조용히 실패하고 CI에서는 실제로 나가서, CI에서만 실패하는 테스트가 두 번 나왔다(rclone 업데이트 확인이 GitHub에 접속해 느려짐, 신규 웹툰 수집이 실제 목록을 받아 옴).
루프백(127.x, ::1, localhost)과 유닉스 소켓은 그대로 쓸 수 있다."""
import socket

MARK = "테스트에서는 외부 네트워크를 쓰지 않는다"

def blocked(action):
    try:
        action()
    except OSError as e:
        assert MARK in str(e), f"차단 이유가 안 보인다: {e}"
        return
    raise AssertionError("외부 접속이 막히지 않았다")

# 이름 조회(aiohttp/urllib/requests 모두 여기를 거친다)와 IP 직접 접속 모두 막는다
blocked(lambda: socket.getaddrinfo("example.com", 80))
blocked(lambda: socket.getaddrinfo("93.184.216.34", 80))
blocked(lambda: socket.create_connection(("93.184.216.34", 80), timeout=1))

# 루프백은 된다(테스트가 자기 안의 서버/클라이언트를 쓸 수 있다)
assert socket.getaddrinfo("localhost", 80) and socket.getaddrinfo("127.0.0.1", 80)
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
client = socket.create_connection(server.getsockname(), timeout=2); client.close(); server.close()
print("ALL OK")
