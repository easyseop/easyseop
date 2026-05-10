from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import (
    get_current_user,
    hash_password,
    login_user,
    logout_user,
    user_count,
    verify_password,
)
from ..database import get_session
from ..models import User
from ..templating import templates

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, session: Session = Depends(get_session), error: str = ""
):
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
    email = email.strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse(
            "/auth/login?error=이메일+또는+비밀번호가+일치하지+않습니다",
            status_code=303,
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request, session: Session = Depends(get_session), error: str = ""
):
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

    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return RedirectResponse(
            "/auth/register?error=이미+가입된+이메일입니다", status_code=303
        )

    is_first = user_count(session) == 0
    user = User(
        email=email,
        password_hash=hash_password(password),
        is_admin=is_first,  # 첫 가입자는 자동 관리자
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    login_user(request, user)

    return RedirectResponse("/" if is_first else "/auth/pending", status_code=303)


@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/pending", response_class=HTMLResponse)
def pending(request: Request, session: Session = Depends(get_session)):
    """비관리자 로그인 유저가 보호 영역 접근 시 도착하는 페이지."""
    user = get_current_user(request, session)
    if user is None:
        return RedirectResponse("/auth/login", status_code=303)
    if user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "auth/pending.html", {"current_user": user}
    )
