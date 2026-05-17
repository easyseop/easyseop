"""유저 개인 설정 (텔레그램 chat_id 등록 등) + 시스템 정보 (DB/저장소)."""
import json
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, func, select

from ..auth import require_admin, require_login
from ..config import AUTH_ENABLED, DATABASE_URL, UPLOAD_DIR
from ..database import get_session
from ..models import (
    Encounter,
    EncounterEvent,
    IntroductionRequest,
    Person,
    PersonRevision,
    Photo,
    User,
)
from ..notifications import BOT_TOKEN, SSL_CONTEXT, send_telegram, telegram_enabled
from ..templating import templates


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _folder_stats(p: Path) -> tuple[int, int]:
    if not p.exists():
        return 0, 0
    total = 0
    count = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
            count += 1
    return total, count


def _db_stats(session: Session) -> dict:
    counts = {
        "User": session.exec(select(func.count()).select_from(User)).one(),
        "Person": session.exec(select(func.count()).select_from(Person)).one(),
        "Photo": session.exec(select(func.count()).select_from(Photo)).one(),
        "Encounter": session.exec(select(func.count()).select_from(Encounter)).one(),
        "EncounterEvent": session.exec(select(func.count()).select_from(EncounterEvent)).one(),
        "PersonRevision": session.exec(select(func.count()).select_from(PersonRevision)).one(),
        "IntroductionRequest": session.exec(select(func.count()).select_from(IntroductionRequest)).one(),
    }
    is_sqlite = DATABASE_URL.startswith("sqlite")
    db_file_path = ""
    db_file_size = 0
    if is_sqlite:
        prefix = "sqlite:///"
        raw = DATABASE_URL[len(prefix):] if DATABASE_URL.startswith(prefix) else ""
        if raw:
            p = Path(raw)
            db_file_path = str(p)
            if p.exists():
                db_file_size = p.stat().st_size

    uploads_size, uploads_count = _folder_stats(UPLOAD_DIR)
    return {
        "kind": "SQLite" if is_sqlite else "외부 (MySQL/Postgres 등)",
        "url_masked": DATABASE_URL if is_sqlite else DATABASE_URL.split("@", 1)[-1],
        "counts": counts,
        "db_file_path": db_file_path,
        "db_file_size": db_file_size,
        "db_file_size_h": _human_bytes(db_file_size) if db_file_size else "—",
        "uploads_path": str(UPLOAD_DIR),
        "uploads_size": uploads_size,
        "uploads_size_h": _human_bytes(uploads_size),
        "uploads_count": uploads_count,
    }

router = APIRouter(prefix="/settings", tags=["settings"])


def _bot_info() -> tuple[str, str]:
    """봇 username 조회 (getMe). 실패 시 빈 문자열."""
    if not BOT_TOKEN:
        return "", "토큰 없음"
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        with urllib.request.urlopen(url, timeout=5, context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read())
        if not data.get("ok"):
            return "", data.get("description", "거부됨")
        return data["result"].get("username", ""), ""
    except urllib.error.HTTPError as e:
        return "", f"HTTP {e.code}"
    except Exception as e:
        return "", f"{e}"


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
        with urllib.request.urlopen(url, timeout=5, context=SSL_CONTEXT) as resp:
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
    bot_username = ""
    bot_error = ""
    if telegram_enabled():
        bot_username, bot_error = _bot_info()
    if detect:
        detected_chats, detect_error = _detect_chats()

    db_info = _db_stats(session) if user.is_admin else None

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_user": user,
            "telegram_enabled_globally": telegram_enabled(),
            "bot_username": bot_username,
            "bot_error": bot_error,
            "detected_chats": detected_chats,
            "detect_error": detect_error,
            "detect_attempted": bool(detect),
            "db_info": db_info,
            "flash": flash,
            "ok": ok,
            "err": err,
        },
    )


@router.post("/nickname")
def save_nickname(
    nickname: str = Form(""),
    current_user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    user = session.get(User, current_user.id)
    if not user:
        return RedirectResponse("/", status_code=303)
    nick = nickname.strip()[:64]
    # 비워서 저장하면 새 랜덤 닉네임으로
    from ..nicknames import random_nickname
    user.nickname = nick or random_nickname()
    session.add(user)
    session.commit()
    return RedirectResponse("/settings?flash=닉네임+저장됨", status_code=303)


@router.post("/nickname/reroll")
def reroll_nickname(
    current_user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    user = session.get(User, current_user.id)
    if not user:
        return RedirectResponse("/", status_code=303)
    from ..nicknames import random_nickname
    user.nickname = random_nickname()
    session.add(user)
    session.commit()
    return RedirectResponse(f"/settings?flash=새+닉네임+'{user.nickname}'", status_code=303)


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
