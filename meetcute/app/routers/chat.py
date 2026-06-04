"""마담뚜 간 임시 대화방 (ChatRoom / ChatMessage).

설계 (확정):
    - 방 생성: 상대 마담뚜를 직접 골라서 생성. 같은 둘 사이에 살아있는 방이 있으면 재사용.
    - 수명: 마지막 메시지로부터 TTL(기본 72h). 메시지 보낼 때마다 expires_at 갱신(sliding).
      reminders.py 청소 루프가 expires_at < now 인 방을 메시지까지 삭제.
    - 권한: 두 참여자만 입장/전송. 책임자(is_owner)는 열람만 가능 (전송 X).
    - 대화는 웹(HTMX 폴링)에서. 새 메시지 시 상대에게 텔레그램 핑 (5분 도배방지).

AUTH=off 모드는 admin 이 LOCAL_ADMIN 1명뿐이라 의미 없음 → /chat 들어가면 redirect.
"""
import os
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, or_, select

from ..auth import require_admin
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import ChatMessage, ChatRoom, User
from ..notifications import send_telegram
from ..services.activity_log import log_activity
from ..templating import templates
from ..url_watcher import current_public_url

router = APIRouter(prefix="/chat", tags=["chat"])

CHAT_TTL_HOURS = int(os.getenv("MEETCUTE_CHAT_TTL_HOURS", "72"))
# 새 메시지 텔레그램 알림 도배방지: 직전 메시지가 이 시간 이내면 알림 스킵.
NOTIFY_THROTTLE_MINUTES = int(os.getenv("MEETCUTE_CHAT_NOTIFY_THROTTLE_MIN", "5"))
MAX_BODY_LEN = 4000


def _ttl_from(now: datetime) -> datetime:
    return now + timedelta(hours=CHAT_TTL_HOURS)


def _active_rooms_for(session: Session, user_id: int) -> list[ChatRoom]:
    now = datetime.utcnow()
    rows = session.exec(
        select(ChatRoom)
        .where(
            or_(ChatRoom.user_a_id == user_id, ChatRoom.user_b_id == user_id),
            ChatRoom.expires_at > now,
        )
        .order_by(ChatRoom.last_message_at.desc())
    ).all()
    return list(rows)


def _get_room_or_404(session: Session, room_id: int) -> ChatRoom:
    room = session.get(ChatRoom, room_id)
    if not room or room.expires_at <= datetime.utcnow():
        raise HTTPException(404, "대화방이 없거나 만료됐습니다")
    return room


def _messages(session: Session, room_id: int) -> list[ChatMessage]:
    return list(
        session.exec(
            select(ChatMessage)
            .where(ChatMessage.room_id == room_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        ).all()
    )


def _user_map(session: Session, ids: set[int]) -> dict[int, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = session.exec(select(User).where(User.id.in_(list(ids)))).all()
    return {u.id: u for u in rows}


@router.get("", response_class=HTMLResponse)
def list_rooms(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    rooms = _active_rooms_for(session, current_user.id)
    # 상대 마담뚜 표시용 + "새 대화" 폼 후보 목록 (나 빼고 admin 전원)
    others = session.exec(
        select(User).where(User.is_admin == True, User.id != current_user.id)  # noqa: E712
    ).all()
    user_ids = set()
    for r in rooms:
        user_ids.add(r.user_a_id)
        user_ids.add(r.user_b_id)
    users = _user_map(session, user_ids)
    # 방별 마지막 메시지 미리보기
    last_preview: dict[int, ChatMessage] = {}
    for r in rooms:
        m = session.exec(
            select(ChatMessage)
            .where(ChatMessage.room_id == r.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        ).first()
        if m:
            last_preview[r.id] = m
    return templates.TemplateResponse(
        request,
        "chat/list.html",
        {
            "rooms": rooms,
            "others": others,
            "users": users,
            "current_user": current_user,
            "last_preview": last_preview,
            "ttl_hours": CHAT_TTL_HOURS,
        },
    )


@router.post("")
def create_room(
    request: Request,
    other_user_id: int = Form(...),
    topic: str = Form(""),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        raise HTTPException(400, "AUTH=off 모드에선 사용 불가")
    if other_user_id == current_user.id:
        raise HTTPException(400, "본인과는 대화방을 만들 수 없습니다")
    other = session.get(User, other_user_id)
    if not other or not other.is_admin:
        raise HTTPException(404, "상대 마담뚜를 찾을 수 없습니다")

    now = datetime.utcnow()
    # 이미 살아있는 방이 있으면 재사용 (순서 무관)
    existing = session.exec(
        select(ChatRoom).where(
            or_(
                (ChatRoom.user_a_id == current_user.id)
                & (ChatRoom.user_b_id == other_user_id),
                (ChatRoom.user_a_id == other_user_id)
                & (ChatRoom.user_b_id == current_user.id),
            ),
            ChatRoom.expires_at > now,
        )
    ).first()
    if existing:
        return RedirectResponse(f"/chat/{existing.id}", status_code=303)

    room = ChatRoom(
        user_a_id=current_user.id,
        user_b_id=other_user_id,
        topic=topic.strip()[:200],
        created_at=now,
        last_message_at=now,
        expires_at=_ttl_from(now),
    )
    session.add(room)
    session.commit()
    session.refresh(room)
    log_activity(
        session, current_user, "chat.create",
        target_type="chat", target_id=room.id,
        summary=f"대화방 생성 → {other.display_name}",
    )
    session.commit()
    return RedirectResponse(f"/chat/{room.id}", status_code=303)


@router.get("/{room_id}", response_class=HTMLResponse)
def view_room(
    room_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    room = _get_room_or_404(session, room_id)
    if not room.has_access(current_user):
        raise HTTPException(403, "이 대화방에 접근할 권한이 없습니다")
    # 참여자가 열람 → 읽음 시각 갱신
    if room.mark_read(current_user.id, datetime.utcnow()):
        session.add(room)
        session.commit()
    msgs = _messages(session, room.id)
    users = _user_map(session, {room.user_a_id, room.user_b_id})
    return templates.TemplateResponse(
        request,
        "chat/room.html",
        {
            "room": room,
            "messages": msgs,
            "users": users,
            "current_user": current_user,
            "is_participant": room.is_participant(current_user),
            "other_user": users.get(room.other_user_id(current_user.id)),
            "other_last_read": room.other_last_read(current_user.id),
            "ttl_hours": CHAT_TTL_HOURS,
        },
    )


@router.get("/{room_id}/messages", response_class=HTMLResponse)
def poll_messages(
    room_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """HTMX 폴링 전용 — 메시지 목록 partial 만 반환."""
    if not AUTH_ENABLED:
        raise HTTPException(404)
    room = _get_room_or_404(session, room_id)
    if not room.has_access(current_user):
        raise HTTPException(403)
    # 폴링 = 방을 보고 있는 것 → 읽음 시각 갱신
    if room.mark_read(current_user.id, datetime.utcnow()):
        session.add(room)
        session.commit()
    msgs = _messages(session, room.id)
    users = _user_map(session, {room.user_a_id, room.user_b_id})
    return templates.TemplateResponse(
        request,
        "chat/_messages.html",
        {
            "messages": msgs, "users": users, "current_user": current_user,
            "other_last_read": room.other_last_read(current_user.id),
        },
    )


@router.post("/{room_id}/send", response_class=HTMLResponse)
def send_message(
    room_id: int,
    request: Request,
    body: str = Form(...),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        raise HTTPException(400, "AUTH=off 모드에선 사용 불가")
    room = _get_room_or_404(session, room_id)
    if not room.is_participant(current_user):
        # 책임자가 열람만 하는 경우 등 — 전송은 참여자만
        raise HTTPException(403, "이 대화방의 참여자만 메시지를 보낼 수 있습니다")
    text = (body or "").strip()
    if not text:
        raise HTTPException(400, "빈 메시지는 보낼 수 없습니다")
    text = text[:MAX_BODY_LEN]

    now = datetime.utcnow()
    prev_last = room.last_message_at
    had_messages = session.exec(
        select(ChatMessage.id).where(ChatMessage.room_id == room.id)
    ).first() is not None

    msg = ChatMessage(
        room_id=room.id, sender_user_id=current_user.id, body=text, created_at=now,
    )
    session.add(msg)
    room.last_message_at = now
    room.expires_at = _ttl_from(now)
    room.mark_read(current_user.id, now)  # 보낸 사람은 자기 메시지까지 읽은 상태
    session.add(room)
    session.commit()

    # 텔레그램 핑 (도배방지): 첫 메시지거나, 직전 메시지가 throttle 시간 이상 전이면
    notify = (not had_messages) or (
        now - prev_last >= timedelta(minutes=NOTIFY_THROTTLE_MINUTES)
    )
    if notify:
        try:
            other = session.get(User, room.other_user_id(current_user.id))
            if other and other.telegram_chat_id:
                _url = current_public_url()
                link = f'{_url}/chat/{room.id}' if _url else f"/chat/{room.id}"
                preview = text if len(text) <= 60 else text[:60] + "…"
                tg = (
                    f"💬 <b>새 메시지 — {current_user.display_name}</b>\n"
                    f"<i>{preview}</i>\n\n"
                    f"→ <a href=\"{link}\">{link}</a> 에서 대화"
                )
                send_telegram(other.telegram_chat_id, tg)
        except Exception:
            pass  # 알림 실패는 전송 흐름 막지 않음

    # HTMX 면 메시지 partial 반환, 아니면 방으로 redirect (JS 꺼진 경우 대비)
    if request.headers.get("HX-Request"):
        msgs = _messages(session, room.id)
        users = _user_map(session, {room.user_a_id, room.user_b_id})
        return templates.TemplateResponse(
            request,
            "chat/_messages.html",
            {
                "messages": msgs, "users": users, "current_user": current_user,
                "other_last_read": room.other_last_read(current_user.id),
            },
        )
    return RedirectResponse(f"/chat/{room.id}", status_code=303)


@router.post("/{room_id}/close")
def close_room(
    room_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """방 수동 종료 = 즉시 삭제 (메시지까지 cascade). 참여자만 가능."""
    if not AUTH_ENABLED:
        raise HTTPException(400, "AUTH=off 모드에선 사용 불가")
    room = session.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(404, "대화방을 찾을 수 없습니다")
    if not room.is_participant(current_user):
        raise HTTPException(403, "참여자만 방을 닫을 수 있습니다")
    log_activity(
        session, current_user, "chat.close",
        target_type="chat", target_id=room.id,
        summary=f"대화방 #{room.id} 닫음 (삭제)",
    )
    session.delete(room)
    session.commit()
    return RedirectResponse("/chat?ok=" + quote("대화방을 닫았습니다"), status_code=303)
