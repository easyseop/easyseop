"""텔레그램 알림. 토큰 미설정 또는 실패해도 앱은 영향 없도록 조용히 무시.

준비:
    1) @BotFather 로 봇 생성 → 토큰 받기
    2) 환경변수 MEETCUTE_TELEGRAM_BOT_TOKEN 에 세팅
    3) 각 admin 이 봇한테 '/start' 한 번 보내야 봇이 그 사람한테 메시지 가능
    4) /settings 에서 본인 chat_id 입력 (@userinfobot 이나 https://api.telegram.org/bot<TOKEN>/getUpdates 로 확인)
"""
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi

logger = logging.getLogger("meetcute.notifications")

BOT_TOKEN = os.getenv("MEETCUTE_TELEGRAM_BOT_TOKEN", "").strip()

# macOS python.org 빌드 등에서 시스템 인증서 못 찾아 SSL 검증 실패하는 케이스 우회.
# certifi 가 제공하는 CA 번들을 명시적으로 사용.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _open(url: str, *, data: bytes | None = None, timeout: int = 5):
    method = "POST" if data is not None else "GET"
    req = urllib.request.Request(url, data=data, method=method)
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT)


def telegram_enabled() -> bool:
    return bool(BOT_TOKEN)


def send_telegram(chat_id: str, text: str) -> tuple[bool, str]:
    """텔레그램 메시지 전송.

    Returns:
        (성공 여부, 실패 시 에러 메시지). 토큰 없거나 chat_id 없으면 조용히 (False, 사유).
    """
    if not BOT_TOKEN:
        return False, "MEETCUTE_TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다."
    if not chat_id:
        return False, "chat_id 가 비어있습니다 (해당 admin이 /settings 에서 등록 안 함)."

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    try:
        with _open(url, data=data) as resp:
            if resp.status == 200:
                return True, ""
            return False, f"Telegram API HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.warning(f"Telegram HTTPError {e.code}: {body}")
        return False, f"HTTP {e.code} ({body or 'no body'})"
    except urllib.error.URLError as e:
        logger.warning(f"Telegram URLError: {e}")
        return False, f"네트워크 오류: {e}"
    except Exception as e:
        logger.warning(f"Telegram unexpected error: {e}")
        return False, f"예상치 못한 오류: {e}"
