"""인증/권한 헬퍼.

전략:
  - 세션 쿠키(서명됨)에 user_id 저장. 무인증 액세스 시 /auth/login 으로 리다이렉트.
  - require_login: 로그인 필수 페이지용 (관리자 아니어도 됨; pending 페이지 등)
  - require_admin: 관리자 전용. 로그인은 됐지만 비관리자면 /auth/pending 으로.
  - 첫 가입자(=DB의 첫 User)는 자동으로 is_admin=True (부트스트랩).
"""
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, func, select

from .database import get_session
from .models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = session.get(User, uid)
    if not user:
        request.session.clear()
        return None
    return user


def require_login(
    request: Request, session: Session = Depends(get_session)
) -> User:
    user = get_current_user(request, session)
    if user is None:
        raise HTTPException(
            status_code=303,
            detail="Login required",
            headers={"Location": "/auth/login"},
        )
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=303,
            detail="Admin access required",
            headers={"Location": "/auth/pending"},
        )
    return user


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["is_admin"] = user.is_admin


def logout_user(request: Request) -> None:
    request.session.clear()
