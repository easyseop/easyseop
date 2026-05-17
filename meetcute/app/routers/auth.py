from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import (
    find_user_by_email,
    get_current_user,
    hash_password,
    login_is_locked,
    login_user,
    logout_user,
    record_login_failure,
    reset_login_failures,
    user_count,
    verify_password,
)
from ..nicknames import random_nickname
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import User
from ..templating import templates

router = APIRouter(prefix="/auth", tags=["auth"])


def _bypass_if_disabled():
    """AUTH_ENABLED=False 일 때 모든 /auth/* 페이지는 그냥 대시보드로."""
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    return None


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, session: Session = Depends(get_session), error: str = ""
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    if get_current_user(request, session):
        return RedirectResponse("/", status_code=303)
    no_users = user_count(session) == 0
    return templates.TemplateResponse(
        request, "auth/login.html", {"error": error, "no_users": no_users}
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    if login_is_locked(request):
        return RedirectResponse(
            "/auth/login?error=시도+너무+많음.+10분+후+다시.",
            status_code=303,
        )
    email = email.strip().lower()
    user = find_user_by_email(session, email)
    if not user or not verify_password(password, user.password_hash):
        record_login_failure(request)
        return RedirectResponse(
            "/auth/login?error=이메일+또는+비밀번호가+일치하지+않습니다",
            status_code=303,
        )
    reset_login_failures(request)
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request, session: Session = Depends(get_session), error: str = ""
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    if get_current_user(request, session):
        return RedirectResponse("/", status_code=303)
    is_first = user_count(session) == 0
    return templates.TemplateResponse(
        request, "auth/register.html", {"error": error, "is_first": is_first}
    )


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    session: Session = Depends(get_session),
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    email = email.strip().lower()
    if not email or "@" not in email:
        return RedirectResponse(
            "/auth/register?error=올바른+이메일을+입력해주세요", status_code=303
        )
    if len(password) < 8:
        return RedirectResponse(
            "/auth/register?error=비밀번호는+최소+8자+이상이어야+합니다", status_code=303
        )
    if password != password_confirm:
        return RedirectResponse(
            "/auth/register?error=비밀번호+확인이+일치하지+않습니다", status_code=303
        )

    existing = find_user_by_email(session, email)
    if existing:
        return RedirectResponse(
            "/auth/register?error=이미+가입된+이메일입니다", status_code=303
        )

    is_first = user_count(session) == 0
    user = User(
        email=email,
        password_hash=hash_password(password),
        nickname=random_nickname(),
        is_admin=is_first,   # 첫 가입자는 자동 관리자
        is_owner=is_first,   # 첫 가입자는 자동 책임자
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    login_user(request, user)

    # 신규 가입자 알림 — 책임자들에게 텔레그램 푸시 (첫 가입자는 제외 — 본인이라)
    if not is_first:
        _notify_owners_new_signup(session, user)

    return RedirectResponse("/" if is_first else "/auth/pending", status_code=303)


def _notify_owners_new_signup(session: Session, new_user: User) -> None:
    try:
        from ..notifications import send_telegram, telegram_enabled
        from ..url_watcher import current_public_url
        if not telegram_enabled():
            return
        owners = session.exec(
            select(User).where(
                User.is_owner == True,  # noqa: E712
                User.telegram_chat_id != "",  # noqa: E712
            )
        ).all()
        if not owners:
            return
        url = current_public_url()
        link = (
            f"\n→ <a href=\"{url}/users\">/users 에서 승인</a>"
            if url else "\n→ /users 에서 승인"
        )
        msg = (
            f"🆕 <b>신규 가입 — 승인 필요</b>\n\n"
            f"<b>닉네임:</b> {new_user.display_name}\n"
            f"<b>이메일:</b> {new_user.email}\n"
            f"<b>가입 시각:</b> {new_user.created_at.strftime('%Y-%m-%d %H:%M')}"
            f"{link}"
        )
        for o in owners:
            try:
                send_telegram(o.telegram_chat_id, msg)
            except Exception:
                pass
    except Exception:
        pass  # 알림 실패는 가입 흐름 막지 않음


@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/pending", response_class=HTMLResponse)
def pending(request: Request, session: Session = Depends(get_session)):
    """비관리자 로그인 유저가 보호 영역 접근 시 도착하는 페이지."""
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    user = get_current_user(request, session)
    if user is None:
        return RedirectResponse("/auth/login", status_code=303)
    if user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "auth/pending.html", {"current_user": user}
    )
