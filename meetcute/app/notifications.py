"""텔레그램 알림. 토큰 미설정 또는 실패해도 앱은 영향 없도록 조용히 무시.

준비:
    1) @BotFather 로 봇 생성 → 토큰 받기
    2) 환경변수 MEETCUTE_TELEGRAM_BOT_TOKEN 에 세팅
    3) 각 admin 이 봇한테 '/start' 한 번 보내야 봇이 그 사람한테 메시지 가능
    4) /settings 에서 본인 chat_id 입력 (@userinfobot 이나 https://api.telegram.org/bot<TOKEN>/getUpdates 로 확인)
"""
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

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


# ─── 사진 전송 (multipart) ──────────────────────────────────────────────
# 업로드 파일은 /uploads 가 로그인 필요라 텔레그램이 URL 로 못 가져옴.
# → 파일 바이트를 multipart/form-data 로 직접 올린다.
CAPTION_MAX = 1024  # 텔레그램 캡션 길이 제한
_TG_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}  # 텔레그램이 photo 로 받는 포맷


def _mp_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _mp_file(boundary: str, name: str, filename: str, content: bytes) -> bytes:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    return head + content + b"\r\n"


def _post_multipart(method: str, fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bool, str]:
    boundary = "----meetcute" + uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += _mp_field(boundary, k, v)
    for field_name, filename, content in files:
        body += _mp_file(boundary, field_name, filename, content)
    body += f"--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
            if resp.status == 200:
                return True, ""
            return False, f"Telegram API HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.warning(f"Telegram {method} HTTPError {e.code}: {body_txt}")
        return False, f"HTTP {e.code} ({body_txt or 'no body'})"
    except Exception as e:
        logger.warning(f"Telegram {method} error: {e}")
        return False, str(e)


def _read_images(image_paths: list[str]) -> list[tuple[str, bytes]]:
    """텔레그램이 받는 포맷만 읽어서 (파일명, 바이트) 리스트로. HEIC 등은 제외."""
    out: list[tuple[str, bytes]] = []
    for path in image_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext not in _TG_PHOTO_EXT:
            continue  # HEIC 등 텔레그램 미지원 포맷 스킵
        try:
            with open(path, "rb") as f:
                out.append((os.path.basename(path), f.read()))
        except OSError:
            continue
    return out


def send_telegram_photos(chat_id: str, image_paths: list[str], caption: str = "") -> tuple[bool, str]:
    """사진 여러 장 전송. 1장이면 sendPhoto, 2장 이상이면 sendMediaGroup(앨범).
    캡션은 첫 사진에 붙음. HEIC 등 미지원 포맷은 자동 제외.
    보낼 수 있는 사진이 없으면 (False, 사유) — 호출측이 텍스트로 폴백."""
    if not BOT_TOKEN:
        return False, "no token"
    if not chat_id:
        return False, "no chat_id"
    imgs = _read_images(image_paths[:10])
    if not imgs:
        return False, "보낼 수 있는 사진 없음 (지원 포맷 아님/파일 없음)"

    cap = caption[:CAPTION_MAX] if caption else ""

    if len(imgs) == 1:
        filename, content = imgs[0]
        fields = {"chat_id": str(chat_id)}
        if cap:
            fields["caption"] = cap
            fields["parse_mode"] = "HTML"
        return _post_multipart("sendPhoto", fields, [("photo", filename, content)])

    media = []
    files = []
    for i, (filename, content) in enumerate(imgs):
        fname = f"file{i}"
        item = {"type": "photo", "media": f"attach://{fname}"}
        if i == 0 and cap:
            item["caption"] = cap
            item["parse_mode"] = "HTML"
        media.append(item)
        files.append((fname, filename, content))
    fields = {"chat_id": str(chat_id), "media": json.dumps(media)}
    return _post_multipart("sendMediaGroup", fields, files)
