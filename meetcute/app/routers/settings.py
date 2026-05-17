"""유저 개인 설정 (텔레그램 chat_id 등록 등)."""
import json
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from ..auth import require_login
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import User
from ..notifications import BOT_TOKEN, send_telegram, telegram_enabled
from ..templating import templates

router = APIRouter(prefix="/settings", tags=["settings"])


def _detect_chats() -> tuple[list[dict], str]:
    """봇 getUpdates 호출해서 봇에 메시지 보낸 사람들의 chat 정보 모음.

    Returns:
        (chats, error). chats: [{"id": "...", "name": "..."}, ...] (중복 제거)
        error: 실패 시 사용자 친화 메시지, 성공 시 빈 문자열.
    """
    if not BOT_TOKEN:
        return [], "MEETCUTE_TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다."
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return [], f"Telegram HTTP {e.code} — 토큰이 잘못됐을 수 있습니다."
    except urllib.error.URLError as e:
        return [], f"네트워크 오류: {e}"
    except Exception as e:
        return [], f"오류: {e}"

    if not data.get("ok"):
        return [], f"Telegram API 거부: {data.get('description', '?')}"

    seen: dict[str, str] = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        sid = str(cid)
        if sid in seen:
            continue
        name = chat.get("first_name") or chat.get("username") or chat.get("title") or "(이름 없음)"
        if chat.get("last_name"):
            name = f"{name} {chat['last_name']}"
        if chat.get("username"):
            name = f"{name} (@{chat['username']})"
        seen[sid] = name
    return [{"id": k, "name": v} for k, v in seen.items()], ""


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    current_user: User = Depends(require_login),
    flash: str = "",
    ok: str = "",
    err: str = "",
    detect: int = 0,
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    user = session.get(User, current_user.id) or current_user

    detected_chats: list[dict] = []
    detect_error = ""
    if detect:
        detected_chats, detect_error = _detect_chats()

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_user": user,
            "telegram_enabled_globally": telegram_enabled(),
            "detected_chats": detected_chats,
            "detect_error": detect_error,
            "detect_attempted": bool(detect),
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
