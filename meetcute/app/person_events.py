"""매물 관련 텔레그램 알림 이벤트.

새 매물 등록 시 — 공개 범위 안의 모든 admin (telegram_chat_id 등록된)에게 알림.
등록자 본인은 노이즈 방지 차원에서 제외.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from .config import AUTH_ENABLED, UPLOAD_DIR
from .database import engine
from .models import Person, PersonAllowedAdmin, PersonVisibility, User
from .notifications import send_telegram, send_telegram_photos, telegram_enabled
from .url_watcher import current_public_url

logger = logging.getLogger("meetcute.person_events")


def _audience_for(session: Session, person: Person, exclude_user_id: Optional[int]) -> list[User]:
    """공개 대상 admin 목록 (telegram_chat_id 등록된)."""
    all_admins = session.exec(
        select(User).where(
            User.is_admin == True,  # noqa: E712
            User.telegram_chat_id != "",  # noqa: E712
        )
    ).all()

    allowed_ids: set[int] = set()
    if person.visibility == PersonVisibility.RESTRICTED:
        rows = session.exec(
            select(PersonAllowedAdmin.user_id).where(
                PersonAllowedAdmin.person_id == person.id
            )
        ).all()
        allowed_ids = set(rows)

    out: list[User] = []
    for u in all_admins:
        if exclude_user_id and u.id == exclude_user_id:
            continue
        if person.visibility == PersonVisibility.PUBLIC:
            out.append(u)
        else:  # RESTRICTED
            if u.is_owner or u.id == person.owner_user_id or u.id in allowed_ids:
                out.append(u)
    return out


def notify_new_person(person_id: int, registered_by_user_id: Optional[int] = None) -> int:
    """공개 대상 admin 들에게 새 매물 알림. 반환: 전송 성공 건수."""
    if not AUTH_ENABLED or not telegram_enabled():
        return 0
    with Session(engine) as session:
        person = session.get(Person, person_id)
        if not person:
            return 0
        registered_by = (
            session.get(User, registered_by_user_id) if registered_by_user_id else None
        )
        audience = _audience_for(session, person, exclude_user_id=registered_by_user_id)
        if not audience:
            return 0

        sender = registered_by.display_name if registered_by else "(시스템)"
        url = current_public_url()
        link = (
            f"\n→ <a href=\"{url}/persons/{person.id}\">매물 자세히 보기</a>"
            if url else f"\n→ 매물 자세히 보기: /persons/{person.id}"
        )
        vis_note = (
            "\n🔒 비공개 매물 (허락된 admin 만 접근)"
            if person.visibility == PersonVisibility.RESTRICTED else ""
        )
        alias_note = f"\n이름: {person.alias}" if person.alias else ""
        ideal_note = f"\n💭 이상형: {person.ideal_type}" if person.ideal_type else ""

        msg = (
            f"🆕 <b>새 매물 등록</b>\n\n"
            f"<b>{person.public_id}</b> · {person.gender.label} · {person.year_label} · {person.height_cm}cm\n"
            f"📍 {person.location}\n"
            f"💼 {person.workplace}"
            f"{alias_note}{ideal_note}\n"
            f"<b>담당:</b> {sender}"
            f"{vis_note}{link}"
        )

        # 사진 첨부 — 최대 5장, 텔레그램 지원 포맷만 (HEIC 등은 자동 제외).
        # send_telegram_photos 가 캡션(=msg)을 첫 사진에 얹어줌.
        photo_paths: list[str] = []
        for ph in sorted(person.photos, key=lambda x: x.order)[:5]:
            p = UPLOAD_DIR / ph.filename
            if p.exists():
                photo_paths.append(str(p))

        sent = 0
        for u in audience:
            try:
                ok = False
                if photo_paths:
                    ok, _ = send_telegram_photos(u.telegram_chat_id, photo_paths, caption=msg)
                if not ok:
                    # 사진 없거나 사진 전송 실패 → 텍스트로 폴백
                    ok, _ = send_telegram(u.telegram_chat_id, msg)
                if ok:
                    sent += 1
            except Exception as e:
                logger.warning(f"notify_new_person send failed for user {u.id}: {e}")
        return sent
