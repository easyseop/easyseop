"""텔레그램 봇 — 매물을 텔레그램 채팅으로 등록할 수 있게 만드는 polling 핸들러.

지원 명령:
  /start    - 봇 소개 + 내 chat_id 보여주기
  /help     - 명령어 목록
  /me       - 연결된 계정 확인
  /register - 새 매물 등록 (대화형 9 단계)
  /cancel   - 진행 중인 작업 취소
  /done     - 사진 입력 단계에서 등록 마무리

활성 조건: AUTH=on + MEETCUTE_TELEGRAM_BOT_TOKEN 설정. 둘 중 하나라도 빠지면 루프 비활성.
state 는 in-memory (재시작 시 진행 중 세션은 소실; 매물 데이터는 commit 된 것만 남음).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from .config import AUTH_ENABLED, UPLOAD_DIR
from .database import engine, next_public_id
from .models import Gender, Person, Photo, User
from .notifications import BOT_TOKEN, SSL_CONTEXT, telegram_enabled

logger = logging.getLogger("meetcute.bot")

POLL_TIMEOUT = 25  # long-poll seconds
TMP_DIR = UPLOAD_DIR / ".tmp_bot"

# Conversation state per chat_id
_sessions: dict[str, dict] = {}

STATE_REG_GENDER = "REG_GENDER"
STATE_REG_NAME = "REG_NAME"          # 이름 (alias 메모용)
STATE_REG_LOCATION = "REG_LOCATION"
STATE_REG_WORKPLACE = "REG_WORKPLACE"
STATE_REG_AGE = "REG_AGE"
STATE_REG_HEIGHT = "REG_HEIGHT"
STATE_REG_PHOTOS = "REG_PHOTOS"

_PROMPTS = {
    STATE_REG_GENDER: "1/7 · 성별? (M / F / OTHER 중 하나)",
    STATE_REG_NAME: "2/7 · 이름? (메모용, 본인만 봄. 없으면 '-')",
    STATE_REG_LOCATION: "3/7 · 사는곳? (예: 서울 마포구)",
    STATE_REG_WORKPLACE: "4/7 · 직장? (예: ○○회사 마케팅팀)",
    STATE_REG_AGE: "5/7 · 나이? (18~99 숫자)",
    STATE_REG_HEIGHT: "6/7 · 키 (cm)? (120~220 숫자)",
    STATE_REG_PHOTOS: (
        "7/7 · 사진을 보내주세요 (최대 5장). "
        "다 보냈으면 /done. 사진 없이도 /done 가능. /cancel 로 취소.\n"
        "💡 이상형/메모는 등록 후 웹 /persons/{id}/edit 에서 추가 가능."
    ),
}


# ── API 헬퍼 ─────────────────────────────────────────────────────────────
def _api(method: str, **params) -> dict:
    """텔레그램 Bot API 동기 호출. 호출자는 asyncio.to_thread 로 감싸세요."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    # dict/list 값은 JSON 으로 직렬화 (reply_markup 등을 위해)
    clean = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            clean[k] = json.dumps(v, ensure_ascii=False)
        else:
            clean[k] = v
    data = urllib.parse.urlencode(clean).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 5, context=SSL_CONTEXT) as resp:
        return json.loads(resp.read())


def _send(chat_id, text: str) -> None:
    try:
        _api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
             disable_web_page_preview="true")
    except Exception as e:
        logger.warning(f"sendMessage failed: {e}")


# ── 도메인 헬퍼 ──────────────────────────────────────────────────────────
def _get_user(chat_id) -> Optional[User]:
    with Session(engine) as s:
        return s.exec(select(User).where(User.telegram_chat_id == str(chat_id))).first()


def _ask_next(chat_id: str) -> None:
    sess = _sessions.get(str(chat_id))
    if not sess:
        return
    prompt = _PROMPTS.get(sess["state"])
    if prompt:
        _send(chat_id, prompt)


def _cleanup_tmp(sess: dict) -> None:
    for p in sess.get("photo_paths", []):
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass


def _full_help_message(user: User) -> str:
    from .url_watcher import current_public_url

    base = current_public_url()
    def link(path: str) -> str:
        if base:
            return f'<a href="{base}{path}">{path}</a>'
        return f"<code>{path}</code>"

    role = "👑 책임자" if user.is_owner else ("관리자" if user.is_admin else "일반 유저")
    msg = (
        f"🤖 <b>meetcute 도움말</b>\n"
        f"본인: <b>{user.display_name}</b> · {role}\n\n"

        f"<b>━ 봇 명령어 ━</b>\n"
        f"/register — 새 매물 등록 (7단계)\n"
        f"/cancel — 진행 중인 등록 취소\n"
        f"/done — 사진 단계에서 등록 마무리\n"
        f"/me — 본인 계정 정보\n"
        f"/start — chat_id 확인\n"
        f"/help — 이 도움말\n\n"

        f"<b>━ 봇이 보내는 알림 ━</b>\n"
        f"📨 새 소개 요청 도착\n"
        f"✅ 보낸 요청 수락됨\n"
        f"❌ 보낸 요청 거절됨\n"
        f"↩️ 받은 요청 취소됨\n"
        f"⏰ 24시간+ 미응답 재알림\n\n"

        f"<b>━ 웹사이트 기능 ━</b>\n"
        f"• 매물 관리 — 등록/수정/삭제, 사진 5장, 변경 이력 자동 기록\n"
        f"• 만남 기록 — 두 매물 매칭, 결과 변화 이력 자동 기록\n"
        f"• 호환성 체크 — 두 매물 비교, 이전 만남 표시\n"
        f"• 소개 요청 — 다른 admin 매물에 소개 요청 → 수락 시 만남 자동 생성\n"
        f"• 활동 통계 — 매물별 만남 횟수 / 매칭 성공 / 잠자는 매물 알림\n"
        f"• 유저 관리 — 가입자 권한 토글 (책임자만)\n"
        f"• 닉네임 / 텔레그램 / DB 상태 — 내정보 페이지\n\n"

        f"<b>━ 웹 주요 경로 ━</b>\n"
        f"홈: {link('/')}\n"
        f"매물 목록: {link('/persons')}\n"
        f"만남 기록: {link('/encounters')}\n"
        f"소개 요청: {link('/requests')}\n"
        f"호환성 체크: {link('/compatibility')}\n"
        f"내정보: {link('/settings')}\n"
        f"전체 매뉴얼: {link('/manual')}\n"
    )
    return msg


# ── 핸들러 ──────────────────────────────────────────────────────────────
def _handle_command(chat_id: str, cmd: str, user: Optional[User]) -> None:
    if cmd == "/start":
        msg = f"🤖 <b>meetcute 봇</b>\n\n당신의 chat_id: <code>{chat_id}</code>\n\n"
        if user:
            msg += (
                f"✅ 연결된 계정: <b>{user.display_name}</b>\n"
                f"명령어 확인: /help"
            )
        else:
            msg += (
                "❌ 아직 웹사이트 계정과 연결 안 됨.\n"
                "1) 웹 <code>/settings</code> 접속\n"
                "2) 위 chat_id 를 텔레그램 chat_id 칸에 붙여넣고 저장\n"
                "3) 돌아와서 /help"
            )
        _send(chat_id, msg)
        return

    if cmd == "/help":
        if not user:
            _send(chat_id, (
                "❌ 먼저 웹 /settings 에서 chat_id 등록이 필요해요.\n"
                "/start 로 본인 chat_id 확인 가능.\n\n"
                "<b>meetcute 는?</b>\n"
                "소개팅 주선용 관리 도구. 매물·만남·매칭 기록을 한 곳에서.\n"
                "여러 admin 이 같이 쓸 수 있고, 다른 admin 매물에 소개 요청 보내기 가능."
            ))
            return
        _send(chat_id, _full_help_message(user))
        return

    if not user:
        _send(chat_id, "먼저 /start 로 chat_id 확인 후 웹 /settings 에서 연결해주세요.")
        return

    if cmd == "/me":
        role = "👑 책임자" if user.is_owner else ("관리자" if user.is_admin else "일반 유저")
        _send(chat_id, f"<b>{user.display_name}</b>\n권한: {role}")
        return

    if cmd == "/cancel":
        sess = _sessions.pop(chat_id, None)
        if sess:
            _cleanup_tmp(sess)
            _send(chat_id, "↩️ 취소했습니다.")
        else:
            _send(chat_id, "취소할 작업이 없어요.")
        return

    if cmd == "/register":
        if not user.is_admin:
            _send(chat_id, "❌ 관리자만 매물 등록 가능합니다. 책임자에게 승급 요청하세요.")
            return
        _sessions[chat_id] = {
            "state": STATE_REG_GENDER,
            "data": {},
            "photo_paths": [],
            "user_id": user.id,
        }
        _send(chat_id, "🆕 새 매물 등록을 시작합니다. /cancel 로 언제든 취소.\n")
        _ask_next(chat_id)
        return

    if cmd == "/done":
        sess = _sessions.get(chat_id)
        if not sess or sess.get("state") != STATE_REG_PHOTOS:
            _send(chat_id, "사진 입력 단계에서만 /done 가능합니다. /register 로 시작.")
            return
        _finalize_registration(chat_id)
        return

    _send(chat_id, f"알 수 없는 명령: {cmd}\n/help 로 명령어 확인")


def _handle_text(chat_id: str, text: str, user: Optional[User]) -> None:
    if text.startswith("/"):
        _handle_command(chat_id, text.split()[0].lower(), user)
        return

    if not user:
        _send(chat_id, "먼저 /start")
        return

    sess = _sessions.get(chat_id)
    if not sess:
        _send(chat_id, "진행 중인 작업이 없어요. /register 로 시작.")
        return

    state = sess["state"]
    data = sess["data"]
    val = text.strip()

    if state == STATE_REG_GENDER:
        g = val.upper()
        if g not in ("M", "F", "OTHER"):
            _send(chat_id, "M / F / OTHER 중 하나로 답해주세요.")
            return
        data["gender"] = g
        sess["state"] = STATE_REG_AGE

    elif state == STATE_REG_AGE:
        try:
            age = int(val)
            assert 18 <= age <= 99
        except (ValueError, AssertionError):
            _send(chat_id, "18~99 사이의 숫자로 답해주세요.")
            return
        data["age"] = age
        sess["state"] = STATE_REG_LOCATION

    elif state == STATE_REG_LOCATION:
        if not val:
            _send(chat_id, "거주지를 입력해주세요.")
            return
        data["location"] = val[:255]
        sess["state"] = STATE_REG_WORKPLACE

    elif state == STATE_REG_WORKPLACE:
        if not val:
            _send(chat_id, "직장을 입력해주세요.")
            return
        data["workplace"] = val[:255]
        sess["state"] = STATE_REG_HEIGHT

    elif state == STATE_REG_HEIGHT:
        try:
            h = int(val)
            assert 120 <= h <= 220
        except (ValueError, AssertionError):
            _send(chat_id, "120~220 사이의 cm 숫자로 답해주세요.")
            return
        data["height_cm"] = h
        sess["state"] = STATE_REG_IDEAL

    elif state in _FIELD_FOR_STATE:
        field, nxt = _FIELD_FOR_STATE[state]
        data[field] = "" if val == "-" else val
        sess["state"] = nxt

    elif state == STATE_REG_PHOTOS:
        _send(chat_id, "사진을 보내거나 /done 으로 등록 완료, /cancel 로 취소하세요.")
        return

    else:
        return

    _ask_next(chat_id)


def _handle_photo(chat_id: str, file_id: str, user: Optional[User]) -> None:
    if not user:
        _send(chat_id, "먼저 /start")
        return
    sess = _sessions.get(chat_id)
    if not sess or sess["state"] != STATE_REG_PHOTOS:
        _send(chat_id, "사진은 매물 등록 사진 단계에서만 받습니다. /register 로 시작.")
        return
    if len(sess["photo_paths"]) >= 5:
        _send(chat_id, "최대 5장까지. /done 으로 등록 마무리하세요.")
        return

    # 1) getFile
    try:
        info = _api("getFile", file_id=file_id)
    except Exception as e:
        _send(chat_id, f"사진 메타 조회 실패: {e}")
        return
    if not info.get("ok"):
        _send(chat_id, f"사진 메타 조회 실패: {info.get('description')}")
        return
    file_path = info["result"].get("file_path")
    if not file_path:
        _send(chat_id, "사진 경로를 못 받았어요.")
        return

    # 2) download
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file_path).suffix.lower() or ".jpg"
    tmp = TMP_DIR / f"{chat_id}_{uuid.uuid4().hex}{ext}"
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=30, context=SSL_CONTEXT) as resp:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
    except Exception as e:
        _send(chat_id, f"사진 다운로드 실패: {e}")
        return

    sess["photo_paths"].append(str(tmp))
    _send(chat_id, f"📷 {len(sess['photo_paths'])}/5 받음. 더 보내거나 /done 으로 마무리.")


def _finalize_registration(chat_id: str) -> None:
    sess = _sessions.pop(chat_id, None)
    if not sess:
        return
    data = sess["data"]
    user_id = sess["user_id"]
    photo_paths = sess["photo_paths"]

    try:
        from PIL import Image, ImageOps

        gender = Gender(data["gender"])
        with Session(engine) as s:
            user = s.get(User, user_id)
            owner_id = user.id if user and user.id else None
            pid = next_public_id(s, gender)
            person = Person(
                public_id=pid,
                gender=gender,
                age=data["age"],
                location=data["location"],
                workplace=data["workplace"],
                height_cm=data["height_cm"],
                ideal_type=data.get("ideal_type", ""),
                notes=data.get("notes", ""),
                alias=data.get("alias", ""),
                owner_user_id=owner_id,
            )
            s.add(person)
            s.commit()
            s.refresh(person)

            person_dir = UPLOAD_DIR / str(person.id)
            person_dir.mkdir(parents=True, exist_ok=True)

            for i, tmp_path in enumerate(photo_paths):
                src = Path(tmp_path)
                ext = src.suffix.lower() or ".jpg"
                name = f"{uuid.uuid4().hex}{ext}"
                dest = person_dir / name
                try:
                    img = Image.open(src)
                    img = ImageOps.exif_transpose(img)
                    if ext in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(dest)
                except Exception:
                    shutil.copy(src, dest)
                src.unlink(missing_ok=True)
                s.add(Photo(person_id=person.id, filename=f"{person.id}/{name}", order=i))
            s.commit()

            _send(chat_id, (
                f"✅ <b>매물 {pid} 등록 완료!</b>\n"
                f"사진 {len(photo_paths)}장.\n"
                f"웹에서 확인: /persons/{person.id}"
            ))
    except Exception as e:
        logger.exception("registration finalize failed")
        _send(chat_id, f"❌ 등록 실패: {e}")
        # tmp 정리
        for p in photo_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def _process_update(upd: dict) -> None:
    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat") or {}
    cid_raw = chat.get("id")
    if cid_raw is None:
        return
    chat_id = str(cid_raw)

    # 비-private chat (group 등) 은 무시 (개인정보 흘릴 위험)
    if chat.get("type") and chat["type"] != "private":
        return

    user = _get_user(chat_id)

    # 사진?
    photos = msg.get("photo")
    if photos:
        largest = photos[-1]
        _handle_photo(chat_id, largest["file_id"], user)
        return

    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        return
    _handle_text(chat_id, text, user)


# ── 루프 ────────────────────────────────────────────────────────────────
async def bot_poll_loop():
    """FastAPI lifespan 백그라운드 task."""
    if not AUTH_ENABLED:
        logger.info("bot loop disabled: AUTH=off")
        return
    if not telegram_enabled():
        logger.info("bot loop disabled: no MEETCUTE_TELEGRAM_BOT_TOKEN")
        return

    logger.info("bot poll loop started")

    # 콜드 스타트: 오래된 메시지 무시. 마지막 update_id+1 로 offset 잡음.
    offset = 0
    try:
        initial = await asyncio.to_thread(_api, "getUpdates", offset=-1)
        if initial.get("ok") and initial.get("result"):
            offset = initial["result"][-1]["update_id"] + 1
    except Exception as e:
        logger.warning(f"cold start failed: {e}")

    while True:
        try:
            data = await asyncio.to_thread(
                _api, "getUpdates",
                offset=offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=["message"],
            )
            if not data.get("ok"):
                logger.warning(f"getUpdates not ok: {data.get('description')}")
                await asyncio.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                try:
                    await asyncio.to_thread(_process_update, upd)
                except Exception as e:
                    logger.exception(f"process_update failed: {e}")
        except urllib.error.URLError as e:
            logger.warning(f"poll URLError: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            logger.exception(f"poll error: {e}")
            await asyncio.sleep(5)
