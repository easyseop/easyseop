"""서버 시작 알림 — 콜드 부팅 시 모든 마담뚜에게 텔레그램 '서버 시작' 1회 발송.

도배 방지:
  - .last_startup_notice_at 파일에 마지막 발송 시각 기록.
  - 30분 안에 이미 보냈으면 스킵 (hot reload / 잦은 git pull 케이스).
  - 따라서 처음 적용되는 시점 (파일 없음) + 콜드 부팅 (맥 재부팅 후 30분+)
    에만 알림이 갑니다.

URL 은 .public_url (tunnel.sh 가 씀) 또는 env. 미설정이면 '(아직 미정)' 표시 —
url_watcher 가 60초 안에 실제 URL 잡으면 별도로 'URL 변경' 알림이 추가로 갑니다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .config import BASE_DIR
from .database import engine
from .models import User
from .notifications import send_telegram, telegram_enabled
from .url_watcher import current_public_url

logger = logging.getLogger("meetcute.startup_notice")

COOLDOWN_FILE = BASE_DIR / ".last_startup_notice_at"
COOLDOWN_MINUTES = 30


def _within_cooldown(now: datetime) -> bool:
    if not COOLDOWN_FILE.exists():
        return False
    try:
        last = datetime.fromisoformat(COOLDOWN_FILE.read_text().strip())
    except Exception:
        return False
    return (now - last) < timedelta(minutes=COOLDOWN_MINUTES)


def _write_marker(now: datetime) -> None:
    try:
        COOLDOWN_FILE.write_text(now.isoformat())
    except Exception as e:
        logger.warning(f"failed to write {COOLDOWN_FILE}: {e}")


def send_startup_notification() -> int:
    """모든 마담뚜에게 '서버 시작' 텔레그램. 반환: 발송 성공 수."""
    if not telegram_enabled():
        logger.info("startup notice skipped: telegram off")
        return 0
    now = datetime.utcnow()
    if _within_cooldown(now):
        logger.info(f"startup notice skipped: within {COOLDOWN_MINUTES}min cooldown")
        return 0

    url = current_public_url() or ""
    url_line = (
        f"접속 URL: <a href=\"{url}\">{url}</a>"
        if url else
        "접속 URL: (잠시 후 별도 알림)"
    )
    msg = (
        f"🚀 <b>meetcute 서버 시작</b>\n\n"
        f"{url_line}\n\n"
        f"잠깐 끊겼다가 다시 켰어요. 정상 동작합니다."
    )

    with Session(engine) as s:
        admins = s.exec(
            select(User).where(
                User.is_admin == True,  # noqa: E712
                User.telegram_chat_id != "",  # noqa: E712
            )
        ).all()
    sent = 0
    for u in admins:
        try:
            ok, _ = send_telegram(u.telegram_chat_id, msg)
            if ok:
                sent += 1
        except Exception as e:
            logger.warning(f"startup notice failed for user {u.id}: {e}")
    if sent:
        _write_marker(now)
        logger.info(f"startup notice sent to {sent} admin(s)")
    return sent
