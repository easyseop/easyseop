"""매물 관련 텔레그램 알림 이벤트.

새 매물 등록 / 공개 대상 확대 시 — 그 매물을 볼 수 있는 마담뚜(telegram 등록된)
중 **아직 알림 안 받은 사람에게만** 알림. 누가 이미 받았는지는 PersonNotified 에
기록해서 중복 발송 방지.

  - 등록 시: PUBLIC 이면 전원(등록자 제외), RESTRICTED 면 허용 대상만.
  - 나중에 공개 대상이 바뀌면(RESTRICTED 허용 목록 추가, 또는 RESTRICTED→PUBLIC):
    새로 볼 수 있게 된 사람에게만 알림. 이미 받은 사람은 스킵.
  - 등록자/수정자는 알림 대상에서 빼되 '이미 앎'으로 기록.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from .config import AUTH_ENABLED, UPLOAD_DIR
from .database import engine
from .models import (
    Person,
    PersonAllowedAdmin,
    PersonNotified,
    PersonVisibility,
    User,
)
from .notifications import send_telegram, send_telegram_photos, telegram_enabled
from .url_watcher import current_public_url

logger = logging.getLogger("meetcute.person_events")


def _eligible_audience(session: Session, person: Person) -> list[User]:
    """이 매물을 볼 수 있고 telegram_chat_id 등록된 admin 전체 (등록자 제외 안 함)."""
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
        if person.visibility == PersonVisibility.PUBLIC:
            out.append(u)
        else:  # RESTRICTED
            if u.is_owner or u.id == person.owner_user_id or u.id in allowed_ids:
                out.append(u)
    return out


def _already_notified_ids(session: Session, person_id: int) -> set[int]:
    rows = session.exec(
        select(PersonNotified.user_id).where(PersonNotified.person_id == person_id)
    ).all()
    return set(rows)


def _record_notified(session: Session, person_id: int, user_ids: set[int]) -> None:
    existing = _already_notified_ids(session, person_id)
    added = False
    for uid in user_ids:
        if uid in existing:
            continue
        session.add(PersonNotified(person_id=person_id, user_id=uid))
        added = True
    if added:
        session.commit()


def _person_photo_paths(person: Person) -> list[str]:
    """텔레그램에 첨부할 사진 파일 경로 (최대 5장). 존재하는 파일만."""
    paths: list[str] = []
    for ph in sorted(person.photos, key=lambda x: x.order)[:5]:
        p = UPLOAD_DIR / ph.filename
        if p.exists():
            paths.append(str(p))
    return paths


def _build_message(person: Person, sender: str, is_new: bool) -> str:
    link_url = current_public_url()
    link = (
        f"\n→ <a href=\"{link_url}/persons/{person.id}\">매물 자세히 보기</a>"
        if link_url else f"\n→ 매물 자세히 보기: /persons/{person.id}"
    )
    vis_note = (
        "\n🔒 비공개 매물 (허락된 마담뚜만 접근)"
        if person.visibility == PersonVisibility.RESTRICTED else ""
    )
    alias_note = f"\n이름: {person.alias}" if person.alias else ""
    ideal_note = f"\n💭 이상형: {person.ideal_type}" if person.ideal_type else ""
    header = "🆕 <b>새 매물 등록</b>" if is_new else "📢 <b>새로 공개된 매물</b> (이제 볼 수 있어요)"
    return (
        f"{header}\n\n"
        f"<b>{person.public_id}</b> · {person.gender.label} · {person.year_label} · {person.height_cm}cm\n"
        f"📍 {person.location}\n"
        f"💼 {person.workplace}"
        f"{alias_note}{ideal_note}\n"
        f"<b>담당:</b> {sender}"
        f"{vis_note}{link}"
    )


def notify_person_audience(
    person_id: int,
    actor_user_id: Optional[int] = None,
    is_new: bool = True,
) -> int:
    """현재 열람 가능자 중 아직 알림 안 받은 사람에게만 알림 + 기록.

    is_new=True  → 신규 등록 ('🆕 새 매물')
    is_new=False → 공개 대상 확대 감지 ('📢 새로 공개된 매물')
    반환: 실제 발송 성공 건수.
    """
    if not AUTH_ENABLED:
        return 0
    with Session(engine) as session:
        person = session.get(Person, person_id)
        if not person:
            return 0

        actor = session.get(User, actor_user_id) if actor_user_id else None
        sender = actor.display_name if actor else "(시스템)"

        # 텔레그램 꺼져 있어도 actor 는 '이미 앎'으로 기록해 나중 재알림 방지.
        if not telegram_enabled():
            if actor_user_id:
                _record_notified(session, person_id, {actor_user_id})
            return 0

        eligible = _eligible_audience(session, person)
        already = _already_notified_ids(session, person_id)
        targets = [
            u for u in eligible
            if u.id not in already and u.id != actor_user_id
        ]

        # 발송 대상이 없어도 actor 는 기록 (자기가 만든/수정한 매물)
        if not targets:
            if actor_user_id:
                _record_notified(session, person_id, {actor_user_id})
            return 0

        msg = _build_message(person, sender, is_new)
        photo_paths = _person_photo_paths(person)

        sent_ids: set[int] = set()
        for u in targets:
            try:
                ok = False
                if photo_paths:
                    ok, _ = send_telegram_photos(u.telegram_chat_id, photo_paths, caption=msg)
                if not ok:
                    ok, _ = send_telegram(u.telegram_chat_id, msg)
                if ok:
                    sent_ids.add(u.id)
            except Exception as e:
                logger.warning(f"notify_person_audience send failed for user {u.id}: {e}")

        record = set(sent_ids)
        if actor_user_id:
            record.add(actor_user_id)
        _record_notified(session, person_id, record)
        return len(sent_ids)


# 하위 호환 별칭 (기존 호출부).
def notify_new_person(person_id: int, registered_by_user_id: Optional[int] = None) -> int:
    return notify_person_audience(person_id, registered_by_user_id, is_new=True)
