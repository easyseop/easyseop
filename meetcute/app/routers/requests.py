"""소개 요청 (IntroductionRequest) 워크플로.

흐름:
    A: 다른 마담뚜 (B) 소유의 매물 M-042 상세 → '📨 소개 요청' 클릭
       → 내 매물 중에서 누구로 소개? 선택 + 메모 → 보내기
    B: /requests 에서 받은 요청 확인 → IRL로 M-042 한테 물어봄
       → 수락 (응답 메모와 함께) → Encounter 자동 생성
       → 또는 거절

AUTH=off 모드에선 admin이 1명뿐(LOCAL_ADMIN)이라 의미 없음 → /requests 들어가면 메시지 + redirect.
"""
from datetime import date, datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, or_, select

from ..auth import require_admin
from ..config import AUTH_ENABLED
from ..database import get_session
from ..url_watcher import current_public_url
from ..models import (
    Encounter,
    EncounterEvent,
    EncounterOutcome,
    IntroductionRequest,
    IntroRequestStatus,
    Person,
    SenderConsentStatus,
    User,
)
from ..notifications import send_telegram, telegram_enabled
from ..services.activity_log import log_activity
from ..templating import templates

router = APIRouter(prefix="/requests", tags=["requests"])


STATUS_LABEL = {
    IntroRequestStatus.PENDING: "대기 중",
    IntroRequestStatus.ACCEPTED: "수락됨 ✅",
    IntroRequestStatus.DECLINED: "거절됨",
    IntroRequestStatus.WITHDRAWN: "취소됨",
}

STATUS_BADGE = {
    IntroRequestStatus.PENDING: "bg-amber-100 text-amber-700",
    IntroRequestStatus.ACCEPTED: "bg-pink-100 text-pink-700",
    IntroRequestStatus.DECLINED: "bg-neutral-100 text-neutral-500",
    IntroRequestStatus.WITHDRAWN: "bg-neutral-100 text-neutral-400",
}

CONSENT_LABEL = {
    SenderConsentStatus.NOT_ASKED: "⚠️ 미확인",
    SenderConsentStatus.AGREED: "✅ 동의",
    SenderConsentStatus.DECLINED: "❌ 거절",
}

CONSENT_BADGE = {
    SenderConsentStatus.NOT_ASKED: "bg-amber-50 text-amber-700 border border-amber-200",
    SenderConsentStatus.AGREED: "bg-emerald-50 text-emerald-700 border border-emerald-200",
    SenderConsentStatus.DECLINED: "bg-neutral-100 text-neutral-500 border border-neutral-200",
}


def _both_agreed_notify_ready(
    session: Session,
    req: IntroductionRequest,
    actor: Optional[User],
) -> bool:
    """양쪽 다 AGREED 면 양쪽에 "연락처 전달 단계" 알림. Encounter 자동 생성 X.

    바뀐 흐름: 양방 AGREED → 알림만. 마담뚜가 카톡 등으로 매물 연락처 직접
    전달한 후, /requests 또는 /encounters 에서 '📞 연락처 전달 완료' 버튼
    눌러야 Encounter 생성됨. 매물 연락처는 앱 DB 안 거침.

    Returns: 양방 AGREED 라 알림 보냈으면 True.
    """
    if req.sender_own_consent != SenderConsentStatus.AGREED:
        return False
    if req.receiver_own_consent != SenderConsentStatus.AGREED:
        return False
    if req.status != IntroRequestStatus.PENDING:
        return False
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    if not my_p or not their_p:
        return False
    from_user = session.get(User, req.from_user_id)
    to_user = session.get(User, req.to_user_id)
    _url = current_public_url()
    link = f'<a href="{_url}/requests">/requests</a>' if _url else "/requests"
    base = (
        f"💞 <b>상호 동의 완료 — 연락처 전달 단계</b>\n\n"
        f"<b>매물 A:</b> {_person_summary(my_p)}\n"
        f"<b>매물 B:</b> {_person_summary(their_p)}\n\n"
        f"이제 카톡/문자/통화로 <b>매물 연락처를 상대 마담뚜에게 직접 전달</b>하세요.\n"
        f"전달이 끝나면 {link} 에서 '📞 연락처 전달 완료' 버튼을 눌러 "
        f"만남 기록을 생성합니다."
    )
    _notify(from_user, base)
    _notify(to_user, base)
    return True


def _create_encounter_from_request(
    session: Session,
    req: IntroductionRequest,
    actor: Optional[User],
) -> Optional[Encounter]:
    """연락처 전달 완료 버튼 클릭 시 호출 — Encounter 생성 + 요청 ACCEPTED.

    호출 시점: 양방 AGREED + status==PENDING. 호출 측이 사전 검증.
    """
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    if not my_p or not their_p:
        return None
    enc_notes = (
        f"📨 소개 요청 양방 동의 + 연락처 전달 완료 → 만남 생성 (req#{req.id})\n"
        f"보낸이: user#{req.from_user_id}"
    )
    if req.message:
        enc_notes += f"\n메모: {req.message}"
    if req.response_note:
        enc_notes += f"\n수신자 응답: {req.response_note}"
    enc = Encounter(
        person_a_id=my_p.id,
        person_b_id=their_p.id,
        person_a_snapshot=my_p.public_id,
        person_b_snapshot=their_p.public_id,
        met_on=date.today(),
        outcome=EncounterOutcome.PENDING,
        notes=enc_notes,
    )
    session.add(enc)
    session.commit()
    session.refresh(enc)
    session.add(EncounterEvent(
        encounter_id=enc.id,
        outcome=EncounterOutcome.PENDING,
        note=f"연락처 전달 완료 → 만남 자동 생성 (req#{req.id})",
        changed_by_user_id=actor.id if actor and actor.id else None,
        changed_by_email=actor.display_name if actor else "",
    ))
    req.status = IntroRequestStatus.ACCEPTED
    req.resolved_encounter_id = enc.id
    req.updated_at = datetime.utcnow()
    session.add(req)
    return enc


def _ctx_extras() -> dict:
    return {
        "STATUS_LABEL": STATUS_LABEL,
        "STATUS_BADGE": STATUS_BADGE,
        "IntroRequestStatus": IntroRequestStatus,
        "CONSENT_LABEL": CONSENT_LABEL,
        "CONSENT_BADGE": CONSENT_BADGE,
        "SenderConsentStatus": SenderConsentStatus,
    }


def _resolve_persons(session: Session, reqs: list[IntroductionRequest]) -> dict[int, Person]:
    ids = set()
    for r in reqs:
        ids.add(r.my_person_id)
        ids.add(r.their_person_id)
    ids.discard(None)
    if not ids:
        return {}
    rows = session.exec(select(Person).where(Person.id.in_(list(ids)))).all()
    return {p.id: p for p in rows}


def _resolve_users(session: Session, reqs: list[IntroductionRequest]) -> dict[int, User]:
    ids = set()
    for r in reqs:
        ids.add(r.from_user_id)
        ids.add(r.to_user_id)
    ids.discard(None)
    if not ids:
        return {}
    rows = session.exec(select(User).where(User.id.in_(list(ids)))).all()
    return {u.id: u for u in rows}


@router.get("", response_class=HTMLResponse)
def list_requests(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)

    incoming = session.exec(
        select(IntroductionRequest)
        .where(IntroductionRequest.to_user_id == current_user.id)
        .order_by(IntroductionRequest.created_at.desc())
    ).all()
    outgoing = session.exec(
        select(IntroductionRequest)
        .where(IntroductionRequest.from_user_id == current_user.id)
        .order_by(IntroductionRequest.created_at.desc())
    ).all()
    all_reqs = list(incoming) + list(outgoing)
    persons = _resolve_persons(session, all_reqs)
    users = _resolve_users(session, all_reqs)
    return templates.TemplateResponse(
        request,
        "requests/list.html",
        {
            "incoming": incoming,
            "outgoing": outgoing,
            "persons": persons,
            "users": users,
            "current_user": current_user,
            **_ctx_extras(),
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_request_form(
    request: Request,
    their: int,  # querystring: their_person_id
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)

    their_person = session.get(Person, their)
    if not their_person:
        raise HTTPException(404, "상대 매물을 찾을 수 없습니다")
    if not their_person.owner_user_id:
        raise HTTPException(400, "이 매물은 마담뚜가 지정되지 않아 요청을 보낼 수 없습니다")
    if their_person.owner_user_id == current_user.id:
        raise HTTPException(400, "본인이 관리하는 매물에는 요청을 보낼 수 없습니다 (직접 만남 기록 만드세요)")

    # 내가 소유한 매물들 중에서 골라야 함
    my_persons = session.exec(
        select(Person)
        .where(Person.owner_user_id == current_user.id)
        .order_by(Person.public_id)
    ).all()
    their_owner = session.get(User, their_person.owner_user_id)
    return templates.TemplateResponse(
        request,
        "requests/new.html",
        {
            "their_person": their_person,
            "their_owner": their_owner,
            "my_persons": my_persons,
            "current_user": current_user,
        },
    )


def _notify(user: Optional[User], text: str) -> None:
    """텔레그램 알림 시도. 실패해도 조용히 무시."""
    if user and user.telegram_chat_id:
        send_telegram(user.telegram_chat_id, text)


def _person_summary(p: Optional[Person]) -> str:
    if not p:
        return "(삭제된 매물)"
    bits = [p.public_id, f"{p.gender.value}", p.year_label, p.location]
    if p.workplace:
        bits.append(p.workplace)
    return " · ".join(bits)


def _requests_link(text: str = "/requests 에서 응답") -> str:
    """텔레그램 HTML 메시지용. live URL (파일/env) 있을 때 클릭 가능 링크."""
    url = current_public_url()
    if url:
        return f'<a href="{url}/requests">{text}</a>'
    return text


@router.post("")
def create_request(
    request: Request,
    my_person_id: int = Form(...),
    their_person_id: int = Form(...),
    message: str = Form(""),
    sender_consent: str = Form(""),  # "1" = 본인 매물 동의 받음, 빈값 = 안 물어봄
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        raise HTTPException(400, "AUTH=off 모드에선 사용 불가")
    if my_person_id == their_person_id:
        raise HTTPException(400, "같은 매물을 양쪽에 둘 수 없습니다")

    my_p = session.get(Person, my_person_id)
    their_p = session.get(Person, their_person_id)
    if not my_p or not their_p:
        raise HTTPException(404, "매물을 찾을 수 없습니다")
    if my_p.owner_user_id != current_user.id:
        raise HTTPException(403, "본인 매물이 아닙니다")
    if not their_p.owner_user_id:
        raise HTTPException(400, "상대 매물에 마담뚜가 없습니다")
    if their_p.owner_user_id == current_user.id:
        raise HTTPException(400, "본인 매물에는 요청을 보낼 수 없습니다")

    consent = (SenderConsentStatus.AGREED if sender_consent == "1"
               else SenderConsentStatus.NOT_ASKED)
    req = IntroductionRequest(
        from_user_id=current_user.id,
        to_user_id=their_p.owner_user_id,
        my_person_id=my_p.id,
        their_person_id=their_p.id,
        message=message.strip(),
        sender_own_consent=consent,
    )
    session.add(req)
    session.commit()
    session.refresh(req)
    log_activity(
        session, current_user, "request.send",
        target_type="request", target_id=req.id,
        summary=f"{my_p.public_id} → {their_p.public_id}",
    )
    session.commit()

    # 알림
    to_user = session.get(User, their_p.owner_user_id)
    msg_text = (
        f"📨 <b>새 소개 요청 도착</b>\n"
        f"<b>보낸 분:</b> {current_user.display_name}\n"
        f"<b>보낸이 매물 의향:</b> {CONSENT_LABEL[consent]}\n\n"
        f"<b>내 매물:</b> {_person_summary(their_p)}\n"
        f"<b>소개 매물:</b> {_person_summary(my_p)}\n"
    )
    if req.message:
        msg_text += f"\n<i>{req.message}</i>\n"
    msg_text += f"\n→ {_requests_link()}"
    _notify(to_user, msg_text)

    return RedirectResponse("/requests", status_code=303)


def _get_owned_request(session: Session, request_id: int, user: User, side: str) -> IntroductionRequest:
    """side='to'(받은측) or 'from'(보낸측). 권한 안 맞으면 403."""
    req = session.get(IntroductionRequest, request_id)
    if not req:
        raise HTTPException(404, "요청을 찾을 수 없습니다")
    if side == "to" and req.to_user_id != user.id:
        raise HTTPException(403, "이 요청의 수신자가 아닙니다")
    if side == "from" and req.from_user_id != user.id:
        raise HTTPException(403, "이 요청의 발신자가 아닙니다")
    return req


@router.post("/{request_id}/accept")
def accept_request(
    request_id: int,
    response_note: str = Form(""),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """B(받은이) 가 본인 매물 동의 표시 = receiver_own_consent → AGREED.

    양방 모두 AGREED 되는 순간에만 status=ACCEPTED + Encounter 자동 생성.
    A 가 아직 동의 표시 안 했으면 → status 는 PENDING 유지, A 에게 알림.
    """
    req = _get_owned_request(session, request_id, current_user, side="to")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")

    req.receiver_own_consent = SenderConsentStatus.AGREED
    if response_note:
        req.response_note = response_note.strip()
    req.updated_at = datetime.utcnow()
    session.add(req)

    both_ready = _both_agreed_notify_ready(session, req, current_user)

    if both_ready:
        log_activity(
            session, current_user, "request.accept",
            target_type="request", target_id=req.id,
            summary=f"#{req.id} 양방 동의 완료 → 연락처 전달 단계",
        )
    else:
        log_activity(
            session, current_user, "request.accept",
            target_type="request", target_id=req.id,
            summary=f"#{req.id} 받은이 매물 동의 (보낸이 동의 대기)",
        )
    session.commit()

    # 발신자에게 알림 (양방 ready 알림은 _both_agreed_notify_ready 가 양쪽에 보냄)
    from_user = session.get(User, req.from_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    if both_ready:
        return RedirectResponse(
            "/requests?ok=" + quote("양방 동의 완료 — 연락처 전달 후 만남 생성"),
            status_code=303,
        )
    else:
        msg = (
            f"✅ <b>받은이 매물 동의 표시</b>\n"
            f"<b>응답한 분:</b> {current_user.display_name}\n\n"
            f"<b>내 매물 (소개 시도):</b> {_person_summary(my_p)}\n"
            f"<b>상대 매물:</b> {_person_summary(their_p)}\n\n"
            f"이제 본인 매물에 의향 물어본 후 /requests 에서 '동의' 표시하세요."
        )
        if response_note:
            msg += f"\n\n<i>{response_note}</i>"
        msg += f"\n→ {_requests_link()}"
        _notify(from_user, msg)
        return RedirectResponse("/requests?ok=" + quote("동의 표시 (보낸이 동의 대기)"), status_code=303)


@router.post("/{request_id}/decline")
def decline_request(
    request_id: int,
    response_note: str = Form(""),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    req = _get_owned_request(session, request_id, current_user, side="to")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    # 받은이 매물 거절 → 양방 동의 안 됐으니 종결
    req.status = IntroRequestStatus.DECLINED
    req.receiver_own_consent = SenderConsentStatus.DECLINED
    req.response_note = response_note.strip()
    req.updated_at = datetime.utcnow()
    session.add(req)
    log_activity(
        session, current_user, "request.decline",
        target_type="request", target_id=req.id,
        summary=f"#{req.id} 받은이 매물 거절 → 종결",
    )
    session.commit()

    from_user = session.get(User, req.from_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"❌ <b>소개 요청 거절됨</b>\n"
        f"<b>응답한 분:</b> {current_user.display_name}\n\n"
        f"<b>내 매물:</b> {_person_summary(my_p)}\n"
        f"<b>상대 매물:</b> {_person_summary(their_p)}\n"
    )
    if response_note:
        msg += f"\n<i>사유: {response_note}</i>\n"
    _notify(from_user, msg)

    return RedirectResponse("/requests", status_code=303)


@router.post("/{request_id}/consent-agree")
def mark_consent_agreed(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """A (보낸이) 가 본인 매물에게 물어봤더니 동의 → sender_own_consent=AGREED.
    양방 모두 AGREED 면 자동 Encounter 생성. 아니면 B 에게 알림만."""
    req = _get_owned_request(session, request_id, current_user, side="from")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    if req.sender_own_consent == SenderConsentStatus.AGREED:
        return RedirectResponse("/requests?flash=이미+동의+표시됨", status_code=303)
    req.sender_own_consent = SenderConsentStatus.AGREED
    req.updated_at = datetime.utcnow()
    session.add(req)
    from ..services.activity_log import log_activity

    both_ready = _both_agreed_notify_ready(session, req, current_user)

    if both_ready:
        log_activity(session, current_user, "request.consent_agreed",
                     target_type="request", target_id=req.id,
                     summary=f"#{req.id} 양방 동의 완료 → 연락처 전달 단계")
    else:
        log_activity(session, current_user, "request.consent_agreed",
                     target_type="request", target_id=req.id,
                     summary=f"#{req.id} 보낸이 매물 동의 (받은이 동의 대기)")
    session.commit()
    # 받은이에게 알림 (양방 ready 알림은 _both_agreed_notify_ready 가 양쪽에 보냄)
    to_user = session.get(User, req.to_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    if both_ready:
        return RedirectResponse(
            "/requests?ok=" + quote("양방 동의 완료 — 연락처 전달 후 만남 생성"),
            status_code=303,
        )
    else:
        msg = (
            f"✅ <b>보낸이가 본인 매물 동의 확인</b>\n"
            f"<b>보낸 분:</b> {current_user.display_name}\n\n"
            f"<b>내 매물:</b> {_person_summary(their_p)}\n"
            f"<b>소개 매물:</b> {_person_summary(my_p)}\n\n"
            f"본인 매물에 물어본 후 /requests 에서 '동의' 표시하세요.\n→ {_requests_link()}"
        )
        _notify(to_user, msg)
        return RedirectResponse("/requests?ok=" + quote("동의 표시 (받은이 동의 대기)"), status_code=303)


@router.post("/{request_id}/consent-decline")
def mark_consent_declined(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """A (보낸이) 가 본인 매물에게 물어봤더니 거절 → consent=DECLINED + 요청 자동 종결 (WITHDRAWN)."""
    req = _get_owned_request(session, request_id, current_user, side="from")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    req.sender_own_consent = SenderConsentStatus.DECLINED
    req.status = IntroRequestStatus.WITHDRAWN
    req.updated_at = datetime.utcnow()
    session.add(req)
    from ..services.activity_log import log_activity
    log_activity(session, current_user, "request.consent_declined",
                 target_type="request", target_id=req.id,
                 summary=f"#{req.id} 본인 매물 거절 → 자동 종결")
    session.commit()
    # 받은이에게 알림
    to_user = session.get(User, req.to_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"❌ <b>소개 요청 자동 종결</b>\n"
        f"<b>보낸 분:</b> {current_user.display_name}\n"
        f"본인 매물이 거절해서 요청 종결.\n\n"
        f"<b>내 매물:</b> {_person_summary(their_p)}\n"
        f"<b>소개 시도된 매물:</b> {_person_summary(my_p)}\n"
    )
    _notify(to_user, msg)
    return RedirectResponse("/requests?ok=요청+자동+종결", status_code=303)


@router.post("/{request_id}/ping-sender")
def ping_sender_to_ask(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """B (받은이) 가 A (보낸이) 에게 '본인 매물에 먼저 물어봐주세요' 회신.
    요청 상태/consent 는 그대로 PENDING/NOT_ASKED 유지. 텔레그램 알림만 보냄."""
    req = _get_owned_request(session, request_id, current_user, side="to")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    if req.sender_own_consent != SenderConsentStatus.NOT_ASKED:
        raise HTTPException(400, "이미 보낸이가 본인 매물 의향 표시함")
    from ..services.activity_log import log_activity
    log_activity(session, current_user, "request.ping_consent",
                 target_type="request", target_id=req.id,
                 summary=f"#{req.id} 보낸이에 의향확인 회신")
    session.commit()
    # A 에게 알림
    from_user = session.get(User, req.from_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"🔔 <b>본인 매물 의향 확인 요청</b>\n"
        f"<b>받은 분:</b> {current_user.display_name}\n"
        f"받은이가 본인 매물에 먼저 물어봐달라고 회신.\n\n"
        f"<b>소개하려던 매물:</b> {_person_summary(my_p)}\n"
        f"<b>상대 매물:</b> {_person_summary(their_p)}\n\n"
        f"본인 매물에 의향 물어본 후 /requests 에서 '동의/거절' 표시.\n→ {_requests_link()}"
    )
    _notify(from_user, msg)
    return RedirectResponse("/requests?ok=의향확인+회신+보냄", status_code=303)


@router.post("/{request_id}/acknowledge")
def acknowledge_request(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """B (받은이) 가 A (보낸이) 에게 '확인했어요, 매물 의향 물어보는 중' 라이트 회신.
    DB 상태/consent 는 그대로 PENDING 유지. 텔레그램 알림만. 여러 번 눌러도 막지 않음."""
    req = _get_owned_request(session, request_id, current_user, side="to")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    from ..services.activity_log import log_activity
    log_activity(session, current_user, "request.acknowledge",
                 target_type="request", target_id=req.id,
                 summary=f"#{req.id} 확인 회신")
    session.commit()
    from_user = session.get(User, req.from_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"👀 <b>요청 확인 — 회신 대기</b>\n"
        f"<b>받은 분:</b> {current_user.display_name}\n"
        f"받은이가 요청을 확인했고, 본인 매물에 의향 물어보는 중입니다.\n\n"
        f"<b>소개한 매물:</b> {_person_summary(my_p)}\n"
        f"<b>상대 매물:</b> {_person_summary(their_p)}\n\n"
        f"답 오는 대로 다시 알림이 와요.\n→ {_requests_link()}"
    )
    _notify(from_user, msg)
    return RedirectResponse("/requests?ok=" + quote("확인 회신 전송"), status_code=303)


@router.post("/{request_id}/send-final-note")
def send_final_note(
    request_id: int,
    note: str = Form(...),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """거절된 요청에 보낸이가 마지막 한 마디 (한 번만). 받은이에게 텔레그램 알림."""
    req = _get_owned_request(session, request_id, current_user, side="from")
    if req.status != IntroRequestStatus.DECLINED:
        raise HTTPException(400, "거절된 요청에만 마지막 한 마디 가능합니다")
    if req.final_note:
        raise HTTPException(400, "이미 마지막 한 마디 보냈습니다 (한 번만 가능)")
    note_clean = note.strip()
    if not note_clean:
        raise HTTPException(400, "빈 메모는 보낼 수 없습니다")
    if len(note_clean) > 500:
        raise HTTPException(400, "500자 이내로 작성해주세요")

    req.final_note = note_clean
    req.updated_at = datetime.utcnow()
    session.add(req)
    from ..services.activity_log import log_activity
    log_activity(session, current_user, "request.final_note",
                 target_type="request", target_id=req.id,
                 summary=f"#{req.id} 거절 후 마지막 한 마디")
    session.commit()

    # 받은이에게 텔레그램
    to_user = session.get(User, req.to_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"💌 <b>거절한 요청에 마지막 한 마디 도착</b>\n"
        f"<b>보낸 분:</b> {current_user.display_name}\n\n"
        f"<b>내 매물:</b> {_person_summary(their_p)}\n"
        f"<b>상대 매물:</b> {_person_summary(my_p)}\n\n"
        f"<i>{note_clean}</i>\n\n"
        f"→ {_requests_link()}"
    )
    _notify(to_user, msg)
    return RedirectResponse("/requests?ok=마지막+한+마디+전송됨", status_code=303)


@router.post("/{request_id}/confirm-contact-exchanged")
def confirm_contact_exchanged(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """양방 동의 완료된 요청에서 연락처 전달 후 누르는 버튼.
    이때 Encounter 자동 생성 + IntroductionRequest 상태 ACCEPTED.
    양쪽 마담뚜 누구나 누를 수 있음."""
    if not AUTH_ENABLED:
        raise HTTPException(400, "AUTH=off 모드에선 사용 불가")
    req = session.get(IntroductionRequest, request_id)
    if not req:
        raise HTTPException(404, "요청을 찾을 수 없습니다")
    if current_user.id not in (req.from_user_id, req.to_user_id):
        raise HTTPException(403, "이 요청의 마담뚜가 아닙니다")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")
    if (req.sender_own_consent != SenderConsentStatus.AGREED
            or req.receiver_own_consent != SenderConsentStatus.AGREED):
        raise HTTPException(400, "양방 동의가 아직 완료되지 않았습니다")

    enc = _create_encounter_from_request(session, req, current_user)
    if not enc:
        raise HTTPException(400, "관련 매물이 삭제됐습니다 — 만남 생성 불가")

    from ..services.activity_log import log_activity
    log_activity(
        session, current_user, "request.contact_exchanged",
        target_type="request", target_id=req.id,
        summary=f"#{req.id} 연락처 전달 완료 → 만남 #{enc.id} 생성",
    )
    session.commit()

    # 상대 마담뚜에게 알림
    other_id = req.to_user_id if current_user.id == req.from_user_id else req.from_user_id
    other_user = session.get(User, other_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    _url = current_public_url()
    enc_link = f'<a href="{_url}/encounters/{enc.id}">#{enc.id}</a>' if _url else f"#{enc.id}"
    msg = (
        f"💞 <b>연락처 전달 완료 → 만남 기록 생성</b>\n"
        f"<b>확인한 분:</b> {current_user.display_name}\n\n"
        f"<b>매물 A:</b> {_person_summary(my_p)}\n"
        f"<b>매물 B:</b> {_person_summary(their_p)}\n\n"
        f"→ 만남 기록 {enc_link}"
    )
    _notify(other_user, msg)
    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


@router.post("/{request_id}/withdraw")
def withdraw_request(
    request_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    req = _get_owned_request(session, request_id, current_user, side="from")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청은 취소할 수 없습니다 ({req.status.value})")
    req.status = IntroRequestStatus.WITHDRAWN
    req.updated_at = datetime.utcnow()
    session.add(req)
    log_activity(
        session, current_user, "request.withdraw",
        target_type="request", target_id=req.id,
        summary=f"#{req.id} 취소",
    )
    session.commit()

    to_user = session.get(User, req.to_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"↩️ <b>소개 요청 취소됨</b>\n"
        f"<b>취소한 분:</b> {current_user.display_name}\n\n"
        f"<b>내 매물:</b> {_person_summary(their_p)}\n"
        f"<b>취소된 소개:</b> {_person_summary(my_p)}\n"
    )
    _notify(to_user, msg)

    return RedirectResponse("/requests", status_code=303)
