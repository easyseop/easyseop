"""필드 수준 암호화 — 개인정보를 DB 에 평문으로 저장 안 함.

암호화 대상 (EncryptedText / IntEncryptedText):
  - User.email, User.password_hash (bcrypt 해시를 한 번 더 감쌈)
  - Person.alias(이름 메모), location, workplace, ideal_type, notes, age, birth_year
  - Encounter.notes, EncounterEvent.note
  - IntroductionRequest.message, response_note, final_note
  - PersonRevision.snapshot_json (과거 값 전체)
  - BlacklistedPair.reason, ActivityLog.summary, ChatMessage.body

비암호화 (필터/정렬/조회에 필요하거나 민감하지 않음):
  - public_id, gender, height_cm, view_count (필터/정렬)
  - nickname, telegram_chat_id
  - Notice.body (앱 기능 공지 — 개인정보 아님)
  - 날짜 / FK / enum

키 관리:
  - `.encryption_key` 파일에 Fernet 키 (chmod 600, gitignored)
  - 첫 부팅 시 자동 생성
  - **이 파일 잃어버리면 암호화된 데이터 복구 불가 — 백업 필수**
  - ⚠️ DB 백업과 **다른 곳**에 보관하세요. 같이 두면 암호화 의미가 없습니다.

백워드 호환:
  - 기존 평문 데이터는 그대로 읽힘 (prefix 가 없으면 평문으로 간주)
  - 부팅 시 `database._encrypt_legacy_plaintext_fields()` 가 평문 행을 일괄 재암호화
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from .config import BASE_DIR

logger = logging.getLogger("meetcute.crypto")

_KEY_FILE = BASE_DIR / ".encryption_key"
_PREFIX = "enc1:"


def _load_or_create_key() -> bytes:
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_bytes().strip()
        if key:
            return key
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except Exception:
        pass
    logger.warning(
        "🔐 새 암호화 키 생성: %s — 이 파일 잃어버리면 암호화된 데이터 복구 불가. 백업 필수.",
        _KEY_FILE,
    )
    return key


_KEY = _load_or_create_key()
_FERNET = Fernet(_KEY)


def encrypt_str(plain: str) -> str:
    """평문 → 'enc1:...' 토큰. 빈 문자열은 그대로 (검색/필터 영향 없음)."""
    if not plain:
        return plain
    token = _FERNET.encrypt(plain.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_str(value: str) -> str:
    """토큰 → 평문. prefix 없으면 평문으로 간주 (백워드 호환)."""
    if not value:
        return value
    if not value.startswith(_PREFIX):
        # 마이그레이션 이전의 평문 데이터
        return value
    try:
        return _FERNET.decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.warning("decrypt InvalidToken — 키가 변경됐거나 데이터 손상")
        return value
    except Exception as e:
        logger.warning(f"decrypt error: {e}")
        return value


class EncryptedText(TypeDecorator):
    """SQLAlchemy 컬럼 타입. write 시 자동 암호화, read 시 자동 복호화."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_str(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_str(value)


class LegacyEncryptedText(TypeDecorator):
    """평문으로 저장하되, 옛 'enc1:' 데이터는 자동 복호화해서 읽음.
    점진 마이그레이션용 — write 가 일어나면 평문으로 덮어쓰여 결국 모두 평문."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # decrypt_str 은 prefix 없으면 그대로 반환 → 평문은 그대로
        return decrypt_str(value)


class IntEncryptedText(TypeDecorator):
    """int 값을 암호화 텍스트로 저장. 기존 INTEGER 평문도 읽음 (SQLite 한정 호환)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_str(str(int(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # 기존 INTEGER 컬럼에서 그대로 읽힌 경우
        if isinstance(value, int):
            return value
        plain = decrypt_str(str(value))
        try:
            return int(plain)
        except (ValueError, TypeError):
            return 0
