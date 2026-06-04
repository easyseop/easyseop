"""24시간 이상 미응답 소개 요청에 텔레그램 재알림 발송.

매시간 한 번 체크하면서:
  - status == PENDING
  - last_reminded_at (없으면 created_at) 이 24시간 이전
인 요청을 찾아 받는이의 telegram_chat_id 로 푸시.

AUTH=off 또는 토큰 미설정 시 루프 자체가 비활성.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .config import AUTH_ENABLED
from .database import engine
from .url_watcher import current_public_url
from .models import (
    Encounter,
    EncounterOutcome,
    IntroductionRequest,
    IntroRequestStatus,
    Person,
    User,
)
from .notifications import send_telegram, telegram_enabled

logger = logging.getLogger("meetcute.reminders")

REMINDER_INTERVAL_HOURS = int(os.getenv("MEETCUTE_REMINDER_HOURS", "24"))
CHECK_INTERVAL_SECONDS = int(os.getenv("MEETCUTE_REMINDER_CHECK_SECONDS", str(60 * 60)))
# 만남 후속 알림 임계값 (일 단위)
ENCOUNTER_PENDING_DAYS = int(os.getenv("MEETCUTE_ENCOUNTER_PENDING_DAYS", "7"))      # PENDING 7일 → "결과 어떻게?"
ENCOUNTER_CONTINUING_DAYS = int(os.getenv("MEETCUTE_ENCOUNTER_CONTINUING_DAYS", "30"))  # CONTINUING 30일 → "혹시 진전?"


def _person_summary(p):
    if not p:
        return "(삭제됨)"
    return f"{p.public_id} ({p.gender.value} {p.year_label} · {p.location})"


def _send_pending_reminders() -> int:
    now = datetime.utcnow()
    threshold = now - timedelta(hours=REMINDER_INTERVAL_HOURS)
    sent = 0
    with Session(engine) as session:
        reqs = session.exec(
            select(IntroductionRequest).where(
                IntroductionRequest.status == IntroRequestStatus.PENDING,
            )
        ).all()
        for r in reqs:
            last = r.last_reminded_at or r.created_at
            if last >= threshold:
                continue
            recipient = session.get(User, r.to_user_id)
            if not recipient or not recipient.telegram_chat_id:
                continue
            my_p = session.get(Person, r.my_person_id)
            their_p = session.get(Person, r.their_person_id)
            sender = session.get(User, r.from_user_id)
            sender_name = sender.display_name if sender else "(?)"
            days_open = max(1, (now - r.created_at).days)
            msg = (
                f"⏰ <b>대기 중인 소개 요청</b> (#{r.id})\n"
                f"{days_open}일째 답변 대기 중\n\n"
                f"<b>보낸 분:</b> {sender_name}\n"
                f"<b>내 매물:</b> {_person_summary(their_p)}\n"
                f"<b>소개 매물:</b> {_person_summary(my_p)}\n"
            )
            if r.message:
                msg += f"\n<i>{r.message}</i>\n"
            _url = current_public_url()
            link = f'<a href="{_url}/requests">/requests</a>' if _url else "/requests"
            msg += f"\n→ {link} 에서 응답 (수락/거절)"
            ok, _ = send_telegram(recipient.telegram_chat_id, msg)
            if ok:
                r.last_reminded_at = now
                session.add(r)
                sent += 1
        if sent:
            session.commit()
    return sent


def _send_encounter_followups() -> int:
    """만남 후속 리마인더 — outcome 별 임계 일수가 지났는데 결과가 안 정해진 케이스.
    PENDING 7일+ → "결과 어떻게?" / CONTINUING 30일+ → "혹시 진전?"
    Person.owner_user_id 양쪽 (둘 다 같은 마담뚜면 한 번) 에게 텔레그램.
    중복 방지를 위해 last_reminded_at 기록 후 같은 임계로는 다시 안 보냄."""
    from datetime import date as _date
    now = datetime.utcnow()
    today = _date.today()
    sent = 0
    with Session(engine) as session:
        encs = session.exec(
            select(Encounter).where(
                Encounter.outcome.in_(
                    [EncounterOutcome.PENDING, EncounterOutcome.CONTINUING]
                )
            )
        ).all()
        for e in encs:
            if not e.met_on:
                continue
            days_since_met = (today - e.met_on).days
            if e.outcome == EncounterOutcome.PENDING:
                threshold = ENCOUNTER_PENDING_DAYS
            else:
                threshold = ENCOUNTER_CONTINUING_DAYS
            if days_since_met < threshold:
                continue
            # 중복 방지: 같은 임계 주기 안에서는 안 보냄 (대략 임계일 만큼 cooldown)
            if e.last_reminded_at and (now - e.last_reminded_at).days < threshold:
                continue

            # 양쪽 매물 + 마담뚜 fetch
            a = session.get(Person, e.person_a_id) if e.person_a_id else None
            b = session.get(Person, e.person_b_id) if e.person_b_id else None
            owners: dict[int, User] = {}
            for p in (a, b):
                if p and p.owner_user_id:
                    u = session.get(User, p.owner_user_id)
                    if u and u.telegram_chat_id and u.id not in owners:
                        owners[u.id] = u
            if not owners:
                continue

            label = ("PENDING (예정/결과 미정)" if e.outcome == EncounterOutcome.PENDING
                     else "CONTINUING (계속 만나는 중)")
            ask = ("결과 어떻게 됐어요?" if e.outcome == EncounterOutcome.PENDING
                   else "혹시 진전 있어요? 결혼 결정 또는 종료?")
            msg = (
                f"💞 <b>만남 #{e.id} 후속 확인</b>\n"
                f"{_person_summary(a)} × {_person_summary(b)}\n"
                f"등록일: {e.met_on} ({days_since_met}일 경과)\n"
                f"현재 상태: {label}\n\n"
                f"<b>{ask}</b>"
            )
            _url = current_public_url()
            link = f'<a href="{_url}/encounters/{e.id}">/encounters/{e.id}</a>' if _url else f"/encounters/{e.id}"
            msg += f"\n→ {link} 에서 결과 업데이트"

            any_ok = False
            for u in owners.values():
                ok, _ = send_telegram(u.telegram_chat_id, msg)
                if ok: any_ok = True
            if any_ok:
                e.last_reminded_at = now
                session.add(e)
                sent += 1
        if sent:
            session.commit()
    return sent


def _purge_expired_chats() -> int:
    """만료된 대화방(expires_at < now) 을 메시지까지 통째로 삭제. 텔레그램과 무관.
    반환: 삭제한 방 개수."""
    from .models import ChatRoom

    now = datetime.utcnow()
    purged = 0
    with Session(engine) as session:
        expired = session.exec(
            select(ChatRoom).where(ChatRoom.expires_at < now)
        ).all()
        for room in expired:
            session.delete(room)  # cascade 로 ChatMessage 도 삭제
            purged += 1
        if purged:
            session.commit()
    return purged


async def reminder_loop():
    """FastAPI lifespan 에서 백그라운드 task 로 실행.

    텔레그램이 꺼져 있어도 대화방 청소(_purge_expired_chats) 는 돌아야 하므로,
    AUTH 만 켜져 있으면 루프를 돌리고 텔레그램 알림은 가능할 때만 보낸다."""
    if not AUTH_ENABLED:
        logger.info("reminder loop disabled: AUTH=off")
        return
    tg = telegram_enabled()
    if not tg:
        logger.info("reminder loop: telegram off — chat purge only")
    else:
        logger.info(
            f"reminder loop started: every {CHECK_INTERVAL_SECONDS}s, "
            f"threshold {REMINDER_INTERVAL_HOURS}h"
        )
    # 첫 체크는 시작 후 1분 뒤 (startup 부담 분리)
    await asyncio.sleep(60)
    while True:
        if tg:
            try:
                n_req = _send_pending_reminders()
                if n_req:
                    logger.info(f"sent {n_req} request reminder(s)")
            except Exception as e:
                logger.exception(f"request reminder loop error: {e}")
            try:
                n_enc = _send_encounter_followups()
                if n_enc:
                    logger.info(f"sent {n_enc} encounter followup(s)")
            except Exception as e:
                logger.exception(f"encounter followup loop error: {e}")
        try:
            n_chat = _purge_expired_chats()
            if n_chat:
                logger.info(f"purged {n_chat} expired chat room(s)")
        except Exception as e:
            logger.exception(f"chat purge loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
