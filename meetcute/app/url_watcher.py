"""터널 URL 변경 감지 + 모든 admin 에게 텔레그램 알림.

동작:
  - 매분 .public_url 파일과 .last_known_url 을 비교
  - 다르면 telegram_chat_id 가 등록된 모든 admin 에게 새 URL 알림
  - .last_known_url 갱신

PUBLIC_URL 결정 우선순위 (전체 시스템 공통):
  1. .public_url 파일이 있으면 그 값 (tunnel.sh 가 자동 갱신)
  2. 없으면 MEETCUTE_PUBLIC_URL 환경변수

토큰/AUTH 없으면 루프 비활성.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session, select

from .config import AUTH_ENABLED, BASE_DIR, PUBLIC_URL
from .database import engine
from .models import User
from .notifications import send_telegram, telegram_enabled

logger = logging.getLogger("meetcute.url_watcher")

URL_FILE = BASE_DIR / ".public_url"
LAST_FILE = BASE_DIR / ".last_known_url"
CHECK_INTERVAL_SECONDS = 60


def current_public_url() -> str:
    """파일이 있으면 거기서, 없으면 env 값. 외부에서도 import 해서 쓸 수 있도록."""
    if URL_FILE.exists():
        try:
            v = URL_FILE.read_text().strip().rstrip("/")
            if v:
                return v
        except Exception:
            pass
    return PUBLIC_URL


def _read_last() -> str:
    if not LAST_FILE.exists():
        return ""
    try:
        return LAST_FILE.read_text().strip().rstrip("/")
    except Exception:
        return ""


def _write_last(url: str) -> None:
    try:
        LAST_FILE.write_text(url)
    except Exception as e:
        logger.warning(f"failed to write {LAST_FILE}: {e}")


def _notify_all_admins(new_url: str, old_url: str) -> int:
    sent = 0
    with Session(engine) as s:
        admins = s.exec(
            select(User).where(
                User.is_admin == True,  # noqa: E712
                User.telegram_chat_id != "",  # noqa: E712
            )
        ).all()
    msg = (
        f"🔄 <b>meetcute 접속 URL 변경</b>\n\n"
        f"새 주소: <a href=\"{new_url}\">{new_url}</a>\n"
    )
    if old_url:
        msg += f"이전 주소: <code>{old_url}</code>\n"
    msg += (
        "\n📱 홈 화면에 추가한 아이콘은 옛 URL 로 가니, "
        "새 URL 로 다시 한 번 \"홈 화면에 추가\" 해주세요."
    )
    for u in admins:
        try:
            ok, _ = send_telegram(u.telegram_chat_id, msg)
            if ok:
                sent += 1
        except Exception as e:
            logger.warning(f"notify failed for user {u.id}: {e}")
    return sent


async def url_watcher_loop():
    if not AUTH_ENABLED:
        logger.info("url watcher disabled: AUTH=off")
        return
    if not telegram_enabled():
        logger.info("url watcher disabled: no MEETCUTE_TELEGRAM_BOT_TOKEN")
        return

    # 시작 시점 URL 을 last 로 박아두기 (첫 부팅에 false-positive 방지)
    boot_url = current_public_url()
    if boot_url and not LAST_FILE.exists():
        _write_last(boot_url)
        logger.info(f"url watcher seeded with {boot_url}")

    logger.info(f"url watcher started (interval={CHECK_INTERVAL_SECONDS}s)")
    while True:
        try:
            current = current_public_url()
            last = _read_last()
            if current and current != last:
                logger.info(f"URL changed: {last!r} → {current!r}")
                n = await asyncio.to_thread(_notify_all_admins, current, last)
                logger.info(f"notified {n} admin(s)")
                _write_last(current)
        except Exception as e:
            logger.exception(f"url_watcher error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
