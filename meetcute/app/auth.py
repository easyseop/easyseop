"""인증/권한 헬퍼.

전략:
  - 세션 쿠키(서명됨)에 user_id 저장. 무인증 액세스 시 /auth/login 으로 리다이렉트.
  - require_login: 로그인 필수 페이지용 (관리자 아니어도 됨; pending 페이지 등)
  - require_admin: 관리자 전용. 로그인은 됐지만 비관리자면 /auth/pending 으로.
  - 첫 가입자(=DB의 첫 User)는 자동으로 is_admin=True (부트스트랩).
  - AUTH_ENABLED=False 이면 모든 보호 의존성이 합성 'local' 관리자를 돌려주고
    인증 검사를 건너뜀. 토글은 config.AUTH_ENABLED 한 줄.
  - 로그인 무차별 대입: 동일 IP 가 LOGIN_LOCKOUT_THRESHOLD 회 실패하면
    LOGIN_LOCKOUT_WINDOW 초 동안 잠금. 인메모리 (재시작 시 리셋).
"""
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, func, select

from .config import AUTH_ENABLED
from .database import get_session
from .models import User


# 인증이 꺼졌을 때 모든 라우터가 받게 되는 가짜 관리자. DB에 저장되지 않음.
LOCAL_ADMIN = User(
    id=0,
    email="(local)",
    password_hash="",
    nickname="(나)",
    is_admin=True,
    is_owner=True,
    created_at=datetime.utcnow(),
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


def find_user_by_email(session: Session, email: str) -> Optional[User]:
    """이메일로 유저 찾기. email 컬럼은 Fernet 으로 암호화돼 있어 (비결정적)
    DB WHERE 가 못 쓰니, 전체 스캔 후 복호화-비교. 마담뚜 수가 작아 비용 무시."""
    norm = email.strip().lower()
    if not norm:
        return None
    for u in session.exec(select(User)).all():
        if (u.email or "").strip().lower() == norm:
            return u
    return None


# ─── 로그인 무차별 대입 차단 (in-memory) ────────────────────────────────────
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_WINDOW = 600  # 초 (10분)
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    """cloudflared 같은 프록시 뒤에서도 진짜 IP 잡기."""
    fwd = request.headers.get("x-forwarded-for") or ""
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def login_is_locked(request: Request) -> bool:
    ip = _client_ip(request)
    now = time.time()
    fresh = [t for t in _failed_attempts[ip] if now - t < LOGIN_LOCKOUT_WINDOW]
    _failed_attempts[ip] = fresh
    return len(fresh) >= LOGIN_LOCKOUT_THRESHOLD


def record_login_failure(request: Request) -> None:
    _failed_attempts[_client_ip(request)].append(time.time())


def reset_login_failures(request: Request) -> None:
    _failed_attempts.pop(_client_ip(request), None)


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    if not AUTH_ENABLED:
        return LOCAL_ADMIN
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = session.get(User, uid)
    if not user:
        request.session.clear()
        return None
    # 세션 캐시된 권한 정보를 매 요청마다 최신화 (책임자가 권한 바꿔도 즉시 반영)
    request.session["is_admin"] = user.is_admin
    request.session["is_owner"] = user.is_owner
    return user


def require_login(
    request: Request, session: Session = Depends(get_session)
) -> User:
    if not AUTH_ENABLED:
        return LOCAL_ADMIN
    user = get_current_user(request, session)
    if user is None:
        raise HTTPException(
            status_code=303,
            detail="Login required",
            headers={"Location": "/auth/login"},
        )
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if not AUTH_ENABLED:
        return LOCAL_ADMIN
    if not user.is_admin:
        raise HTTPException(
            status_code=303,
            detail="Admin access required",
            headers={"Location": "/auth/pending"},
        )
    return user


def require_owner(user: User = Depends(require_admin)) -> User:
    """책임자 전용 페이지 (유저 관리 등). AUTH=off 면 LOCAL_ADMIN 항상 통과."""
    if not AUTH_ENABLED:
        return LOCAL_ADMIN
    if not user.is_owner:
        from urllib.parse import quote
        # HTTP 헤더는 latin-1 → 한글 쿼리스트링은 URL-encode 필수
        raise HTTPException(
            status_code=303,
            detail="책임자 권한 필요",
            headers={"Location": "/?err=" + quote("책임자만 접근 가능합니다")},
        )
    return user


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["is_admin"] = user.is_admin
    request.session["is_owner"] = user.is_owner


def logout_user(request: Request) -> None:
    request.session.clear()
