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

from .config import AUTH_ENABLED, PUBLIC_URL
from .database import engine
from .models import IntroductionRequest, IntroRequestStatus, Person, User
from .notifications import send_telegram, telegram_enabled

logger = logging.getLogger("meetcute.reminders")

REMINDER_INTERVAL_HOURS = int(os.getenv("MEETCUTE_REMINDER_HOURS", "24"))
CHECK_INTERVAL_SECONDS = int(os.getenv("MEETCUTE_REMINDER_CHECK_SECONDS", str(60 * 60)))


def _person_summary(p):
    if not p:
        return "(삭제됨)"
    return f"{p.public_id} ({p.gender.value} {p.age}세 · {p.location})"


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
            link = f'<a href="{PUBLIC_URL}/requests">/requests</a>' if PUBLIC_URL else "/requests"
            msg += f"\n→ {link} 에서 응답 (수락/거절)"
            ok, _ = send_telegram(recipient.telegram_chat_id, msg)
            if ok:
                r.last_reminded_at = now
                session.add(r)
                sent += 1
        if sent:
            session.commit()
    return sent


async def reminder_loop():
    """FastAPI lifespan 에서 백그라운드 task 로 실행."""
    if not AUTH_ENABLED:
        logger.info("reminder loop disabled: AUTH=off")
        return
    if not telegram_enabled():
        logger.info("reminder loop disabled: no MEETCUTE_TELEGRAM_BOT_TOKEN")
        return
    logger.info(
        f"reminder loop started: every {CHECK_INTERVAL_SECONDS}s, "
        f"threshold {REMINDER_INTERVAL_HOURS}h"
    )
    # 첫 체크는 시작 후 1분 뒤 (startup 부담 분리)
    await asyncio.sleep(60)
    while True:
        try:
            n = _send_pending_reminders()
            if n:
                logger.info(f"sent {n} reminder(s)")
        except Exception as e:
            logger.exception(f"reminder loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
