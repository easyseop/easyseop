from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import (
    PASSWORD_RESET_TOKEN_TTL_MINUTES,
    consume_password_reset_token,
    create_password_reset_code,
    create_password_reset_token,
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
from ..services.activity_log import log_activity
from ..templating import templates

router = APIRouter(prefix="/auth", tags=["auth"])


def _bypass_if_disabled():
    """AUTH_ENABLED=False 일 때 모든 /auth/* 페이지는 그냥 대시보드로."""
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    return None


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, session: Session = Depends(get_session),
    error: str = "", email: str = "",
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    if get_current_user(request, session):
        return RedirectResponse("/", status_code=303)
    no_users = user_count(session) == 0
    return templates.TemplateResponse(
        request, "auth/login.html",
        {"error": error, "email": email, "no_users": no_users},
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
    from urllib.parse import quote
    email = email.strip().lower()
    email_qs = "&email=" + quote(email) if email else ""
    if login_is_locked(request):
        return RedirectResponse(
            "/auth/login?error=" + quote("시도 너무 많음. 10분 후 다시.") + email_qs,
            status_code=303,
        )
    user = find_user_by_email(session, email)
    if not user or not verify_password(password, user.password_hash):
        record_login_failure(request)
        return RedirectResponse(
            "/auth/login?error=" + quote("이메일 또는 비밀번호가 일치하지 않습니다") + email_qs,
            status_code=303,
        )
    reset_login_failures(request)
    login_user(request, user)
    log_activity(session, user, "login")
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request, session: Session = Depends(get_session),
    error: str = "", email: str = "",
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    if get_current_user(request, session):
        return RedirectResponse("/", status_code=303)
    is_first = user_count(session) == 0
    return templates.TemplateResponse(
        request, "auth/register.html",
        {"error": error, "email": email, "is_first": is_first},
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
    from urllib.parse import quote
    email = email.strip().lower()
    email_qs = "&email=" + quote(email) if email else ""
    if not email or "@" not in email:
        return RedirectResponse(
            "/auth/register?error=" + quote("올바른 이메일을 입력해주세요") + email_qs,
            status_code=303,
        )
    if len(password) < 8:
        return RedirectResponse(
            "/auth/register?error=" + quote("비밀번호는 최소 8자 이상이어야 합니다") + email_qs,
            status_code=303,
        )
    if password != password_confirm:
        return RedirectResponse(
            "/auth/register?error=" + quote("비밀번호 확인이 일치하지 않습니다") + email_qs,
            status_code=303,
        )

    existing = find_user_by_email(session, email)
    if existing:
        return RedirectResponse(
            "/auth/register?error=" + quote("이미 가입된 이메일입니다") + email_qs,
            status_code=303,
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
    log_activity(
        session, user, "user.register",
        target_type="user", target_id=user.id,
        summary=f"신규 가입{'(첫 가입자 → 책임자 자동승격)' if is_first else ''}",
    )
    session.commit()

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
def logout(request: Request, session: Session = Depends(get_session)):
    user = get_current_user(request, session)
    if user and user.id:
        log_activity(session, user, "logout")
        session.commit()
    logout_user(request)
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(
    request: Request, session: Session = Depends(get_session),
    error: str = "", ok: str = "", email: str = "",
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    return templates.TemplateResponse(
        request, "auth/forgot.html",
        {"error": error, "ok": ok, "email": email,
         "ttl_minutes": PASSWORD_RESET_TOKEN_TTL_MINUTES},
    )


@router.post("/forgot")
def forgot_submit(
    request: Request,
    email: str = Form(...),
    delivery: str = Form("link"),  # "link" = URL via telegram, "code" = 6-digit code
    session: Session = Depends(get_session),
):
    """이메일 입력 → delivery 에 따라 텔레그램 링크 or 6자리 코드 발송.
    이메일 enumeration 방지: 일치/불일치 / 텔레그램 미연동 모두 동일 응답."""
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    from urllib.parse import quote
    from ..notifications import send_telegram, telegram_enabled
    from ..url_watcher import current_public_url

    email_norm = (email or "").strip().lower()
    user = find_user_by_email(session, email_norm)
    can_send = bool(user and user.telegram_chat_id and telegram_enabled())

    if delivery == "owner":
        # 텔레그램 미연동 마담뚜용: 책임자들에게 비번 리셋 요청 알림.
        # 책임자가 /users 에서 직접 리셋 후 임시 비번을 외부 채널로 전달.
        try:
            if telegram_enabled():
                owners = session.exec(
                    select(User).where(
                        User.is_owner == True,  # noqa: E712
                        User.telegram_chat_id != "",  # noqa: E712
                    )
                ).all()
                url = current_public_url()
                users_link = f"{url}/users" if url else "/users"
                msg = (
                    f"🔑 <b>비밀번호 리셋 요청 (텔레그램 미연동 마담뚜)</b>\n\n"
                    f"<b>요청 이메일:</b> <code>{email_norm}</code>\n\n"
                    f"본인 확인 후 <a href=\"{users_link}\">/users</a> 에서 "
                    f"<b>🔑 비번 리셋</b> 클릭 → 임시 비번을 외부 채널(전화/SMS) 로 전달.\n"
                    f"⚠️ 본인 맞는지 다른 방법으로 검증 후 진행."
                )
                for o in owners:
                    try:
                        send_telegram(o.telegram_chat_id, msg)
                    except Exception:
                        pass
            if user:
                log_activity(session, user, "user.password_reset_request_owner",
                             target_type="user", target_id=user.id,
                             summary="비번 리셋 요청 — 책임자 직접 처리")
                session.commit()
        except Exception:
            pass
        ok_msg2 = (
            "책임자에게 비번 리셋 요청이 전달됐어요. "
            "본인 확인 후 책임자가 임시 비번을 외부 채널(전화/SMS 등)로 전달해드립니다."
        )
        return RedirectResponse(
            "/auth/forgot?ok=" + quote(ok_msg2), status_code=303,
        )

    if delivery == "code":
        # 코드 방식: 텔레그램으로 6자리 코드 발송 → /auth/forgot/code 페이지에서 입력
        if can_send:
            try:
                code = create_password_reset_code(session, user)
                msg = (
                    f"🔢 <b>비밀번호 재설정 코드</b>\n\n"
                    f"인증 코드: <code>{code}</code>\n\n"
                    f"이 코드는 {PASSWORD_RESET_TOKEN_TTL_MINUTES}분 동안 유효. "
                    f"웹 페이지 (/auth/forgot/code) 에 입력하세요. "
                    f"본인이 요청한 게 아니면 무시."
                )
                send_telegram(user.telegram_chat_id, msg)
                log_activity(session, user, "user.password_reset_request",
                             target_type="user", target_id=user.id,
                             summary="비번 재설정 6자리 코드 발송")
                session.commit()
            except Exception:
                pass
        return RedirectResponse(
            "/auth/forgot/code?email=" + quote(email_norm),
            status_code=303,
        )

    # 기본 = 링크 방식
    ok_msg = (
        "이메일이 등록돼 있고 텔레그램 연동돼 있으면 재설정 링크를 보냈어요. "
        f"{PASSWORD_RESET_TOKEN_TTL_MINUTES}분 안에 클릭하세요. "
        "오지 않으면 책임자에게 문의해주세요."
    )
    if can_send:
        try:
            raw = create_password_reset_token(session, user)
            url = current_public_url()
            link = f"{url}/auth/reset/{raw}" if url else f"/auth/reset/{raw}"
            msg = (
                f"🔑 <b>비밀번호 재설정 요청</b>\n\n"
                f"<a href=\"{link}\">{link}</a>\n\n"
                f"이 링크는 {PASSWORD_RESET_TOKEN_TTL_MINUTES}분 동안 유효. "
                f"본인이 요청한 게 아니면 무시하세요 (요청한 사람만 링크 알 수 있음)."
            )
            send_telegram(user.telegram_chat_id, msg)
            log_activity(session, user, "user.password_reset_request",
                         target_type="user", target_id=user.id,
                         summary="비번 재설정 토큰 발급 (텔레그램 전송)")
            session.commit()
        except Exception:
            pass
    return RedirectResponse("/auth/forgot?ok=" + quote(ok_msg), status_code=303)


@router.get("/forgot/code", response_class=HTMLResponse)
def forgot_code_form(
    request: Request, session: Session = Depends(get_session),
    email: str = "", error: str = "",
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    return templates.TemplateResponse(
        request, "auth/forgot_code.html",
        {"email": email, "error": error,
         "ttl_minutes": PASSWORD_RESET_TOKEN_TTL_MINUTES},
    )


@router.post("/forgot/code")
def forgot_code_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    session: Session = Depends(get_session),
):
    """이메일 + 6자리 코드 + 새 비번 → 검증 + 비번 변경 + 자동 로그인.
    한 페이지 안에서 완결되는 흐름 (링크 클릭 없음)."""
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    from urllib.parse import quote
    if login_is_locked(request):
        return RedirectResponse(
            "/auth/forgot/code?email=" + quote(email)
            + "&error=" + quote("시도 너무 많음. 10분 후 다시."),
            status_code=303,
        )
    if len(new_password) < 10:
        return RedirectResponse(
            "/auth/forgot/code?email=" + quote(email)
            + "&error=" + quote("비밀번호는 최소 10자 이상"),
            status_code=303,
        )
    if new_password != new_password_confirm:
        return RedirectResponse(
            "/auth/forgot/code?email=" + quote(email)
            + "&error=" + quote("비밀번호 확인이 일치하지 않습니다"),
            status_code=303,
        )
    email_norm = (email or "").strip().lower()
    code_norm = (code or "").strip()
    user = find_user_by_email(session, email_norm)
    consumed = (
        consume_password_reset_token(session, code_norm, user=user)
        if user else None
    )
    if not consumed:
        record_login_failure(request)
        return RedirectResponse(
            "/auth/forgot/code?email=" + quote(email_norm)
            + "&error=" + quote("코드가 잘못됐거나 만료됨"),
            status_code=303,
        )
    reset_login_failures(request)
    consumed.password_hash = hash_password(new_password)
    session.add(consumed)
    log_activity(session, consumed, "user.password_reset",
                 target_type="user", target_id=consumed.id,
                 summary="비번 재설정 완료 (코드 방식)")
    session.commit()
    login_user(request, consumed)
    return RedirectResponse(
        "/?ok=" + quote("비밀번호 재설정 완료 — 로그인됨"),
        status_code=303,
    )


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(
    request: Request, token: str,
    session: Session = Depends(get_session),
    error: str = "",
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    # GET 은 token 소모하지 않고 검증만. 실제 소모는 POST.
    from ..auth import _hash_reset_token
    from ..models import PasswordResetToken
    h = _hash_reset_token(token)
    t = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == h,
            PasswordResetToken.used_at == None,  # noqa: E711
        )
    ).first()
    valid = bool(t and datetime.utcnow() <= t.expires_at)
    return templates.TemplateResponse(
        request, "auth/reset.html",
        {"token": token, "valid": valid, "error": error},
    )


@router.post("/reset/{token}")
def reset_submit(
    request: Request, token: str,
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    session: Session = Depends(get_session),
):
    if (resp := _bypass_if_disabled()) is not None:
        return resp
    from urllib.parse import quote
    if len(new_password) < 10:
        return RedirectResponse(
            f"/auth/reset/{token}?error=" + quote("비밀번호는 최소 10자 이상이어야 합니다"),
            status_code=303,
        )
    if new_password != new_password_confirm:
        return RedirectResponse(
            f"/auth/reset/{token}?error=" + quote("비밀번호 확인이 일치하지 않습니다"),
            status_code=303,
        )
    user = consume_password_reset_token(session, token)
    if not user:
        return RedirectResponse(
            "/auth/forgot?error=" + quote("링크가 만료됐거나 이미 사용됨"),
            status_code=303,
        )
    user.password_hash = hash_password(new_password)
    session.add(user)
    log_activity(session, user, "user.password_reset",
                 target_type="user", target_id=user.id,
                 summary="비번 재설정 완료")
    session.commit()
    login_user(request, user)
    return RedirectResponse("/?ok=" + quote("비밀번호 재설정 완료 — 로그인됨"), status_code=303)


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
