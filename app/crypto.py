"""
DB에 저장하는 민감값(디스코드 웹훅 URL/봇 토큰)을 암호화/복호화한다.

키는 env var로 받지 않는다 — 사용자가 관리할 게 하나라도 늘면 그만큼 실수 여지가
생기기 때문에, DB 파일과 같은 데이터 볼륨 안에 키 파일을 두고 최초 실행 시
자동 생성한다. 키 파일과 DB 파일이 항상 같은 볼륨에 있으므로, 볼륨 자체가
털리면 어차피 암호화도 의미가 없다는 전제는 동일하다 — 이 암호화는 'DB 파일만
따로 유출되는 경우'(백업 실수, 잘못된 공유 등)에 대한 방어선이다.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_fernet: Fernet | None = None


def _key_path() -> Path:
    db_dir = Path(get_settings().database_path).parent
    return db_dir / ".secret.key"


def _load_or_create_key() -> bytes:
    path = _key_path()
    if path.is_file():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # 일부 파일시스템(예: 특정 볼륨 마운트)에서는 권한 변경이 안 될 수 있음 — 치명적이지 않음
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # 키 파일이 바뀌었거나(볼륨 재설정 등) 데이터가 암호화되기 전 버전의 평문일 수 있다.
        # 크래시 대신 빈 값으로 처리해서 "설정 안 됨"으로 안전하게 취급한다.
        return ""
