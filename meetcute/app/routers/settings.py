"""유저 개인 설정 (텔레그램 chat_id 등록 등)."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from ..auth import require_login
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import User
from ..notifications import send_telegram, telegram_enabled
from ..templating import templates

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    current_user: User = Depends(require_login),
    flash: str = "",
    ok: str = "",
    err: str = "",
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    # session 에서 다시 가져옴 (DB 최신 값 반영)
    user = session.get(User, current_user.id) or current_user
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_user": user,
            "telegram_enabled_globally": telegram_enabled(),
            "flash": flash,
            "ok": ok,
            "err": err,
        },
    )


@router.post("/telegram")
def save_telegram(
    chat_id: str = Form(""),
    current_user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    user = session.get(User, current_user.id)
    if not user:
        return RedirectResponse("/", status_code=303)
    user.telegram_chat_id = chat_id.strip()
    session.add(user)
    session.commit()
    return RedirectResponse("/settings?flash=텔레그램+chat_id+저장됨", status_code=303)


@router.post("/telegram/test")
def test_telegram(
    current_user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    user = session.get(User, current_user.id)
    if not user:
        return RedirectResponse("/", status_code=303)
    if not telegram_enabled():
        return RedirectResponse(
            "/settings?err=MEETCUTE_TELEGRAM_BOT_TOKEN+환경변수가+서버에+설정되지+않았습니다",
            status_code=303,
        )
    if not user.telegram_chat_id:
        return RedirectResponse(
            "/settings?err=chat_id+먼저+저장하세요",
            status_code=303,
        )
    ok, msg = send_telegram(
        user.telegram_chat_id,
        "✅ <b>meetcute 테스트 메시지</b>\n알림 연결 OK!",
    )
    if ok:
        return RedirectResponse("/settings?ok=테스트+메시지+전송+성공", status_code=303)
    # URL-encoded 메시지로 전달 (간단히)
    from urllib.parse import quote
    return RedirectResponse(
        f"/settings?err={quote('전송 실패: ' + msg)}", status_code=303
    )
