"""소개 요청 (IntroductionRequest) 워크플로.

흐름:
    A: 다른 admin (B) 소유의 매물 M-042 상세 → '📨 소개 요청' 클릭
       → 내 매물 중에서 누구로 소개? 선택 + 메모 → 보내기
    B: /requests 에서 받은 요청 확인 → IRL로 M-042 한테 물어봄
       → 수락 (응답 메모와 함께) → Encounter 자동 생성
       → 또는 거절

AUTH=off 모드에선 admin이 1명뿐(LOCAL_ADMIN)이라 의미 없음 → /requests 들어가면 메시지 + redirect.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, or_, select

from ..auth import require_admin
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import (
    Encounter,
    EncounterEvent,
    EncounterOutcome,
    IntroductionRequest,
    IntroRequestStatus,
    Person,
    User,
)
from ..notifications import send_telegram, telegram_enabled
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


def _ctx_extras() -> dict:
    return {
        "STATUS_LABEL": STATUS_LABEL,
        "STATUS_BADGE": STATUS_BADGE,
        "IntroRequestStatus": IntroRequestStatus,
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
        raise HTTPException(400, "이 매물은 관리자가 지정되지 않아 요청을 보낼 수 없습니다")
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
    bits = [p.public_id, f"{p.gender.value}", f"{p.age}세", p.location]
    if p.workplace:
        bits.append(p.workplace)
    return " · ".join(bits)


@router.post("")
def create_request(
    request: Request,
    my_person_id: int = Form(...),
    their_person_id: int = Form(...),
    message: str = Form(""),
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
        raise HTTPException(400, "상대 매물에 관리자가 없습니다")
    if their_p.owner_user_id == current_user.id:
        raise HTTPException(400, "본인 매물에는 요청을 보낼 수 없습니다")

    req = IntroductionRequest(
        from_user_id=current_user.id,
        to_user_id=their_p.owner_user_id,
        my_person_id=my_p.id,
        their_person_id=their_p.id,
        message=message.strip(),
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    # 알림
    to_user = session.get(User, their_p.owner_user_id)
    msg_text = (
        f"📨 <b>새 소개 요청</b>\n"
        f"<b>From:</b> {current_user.email}\n"
        f"<b>너네 매물:</b> {_person_summary(their_p)}\n"
        f"<b>소개하려는 매물:</b> {_person_summary(my_p)}\n"
    )
    if req.message:
        msg_text += f"<b>메모:</b> {req.message}\n"
    msg_text += f"\n→ /requests 에서 응답"
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
    req = _get_owned_request(session, request_id, current_user, side="to")
    if req.status != IntroRequestStatus.PENDING:
        raise HTTPException(400, f"이미 처리된 요청입니다 ({req.status.value})")

    # 만남 기록 자동 생성 (PENDING 상태로 — 아직 실제 만남은 안 일어났으니)
    my_p = session.get(Person, req.my_person_id)       # 발신자가 소개한 매물
    their_p = session.get(Person, req.their_person_id) # 수신자(나)의 매물
    if not my_p or not their_p:
        raise HTTPException(400, "관련 매물이 삭제된 상태라 만남 기록을 만들 수 없습니다")

    enc_notes = f"📨 소개 요청 수락\n발신자: {req.from_user_id}\n메모: {req.message}"
    if response_note:
        enc_notes += f"\n수락 응답: {response_note}"
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
        note=f"소개 요청 수락으로 자동 생성 (req#{req.id})",
        changed_by_user_id=current_user.id,
        changed_by_email=current_user.email,
    ))

    req.status = IntroRequestStatus.ACCEPTED
    req.response_note = response_note.strip()
    req.resolved_encounter_id = enc.id
    req.updated_at = datetime.utcnow()
    session.add(req)
    session.commit()

    # 발신자에게 알림
    from_user = session.get(User, req.from_user_id)
    msg = (
        f"✅ <b>소개 요청 수락됨</b>\n"
        f"<b>By:</b> {current_user.email}\n"
        f"<b>매칭:</b> {_person_summary(my_p)} × {_person_summary(their_p)}\n"
    )
    if response_note:
        msg += f"<b>응답 메모:</b> {response_note}\n"
    msg += f"\n→ 만남 기록 자동 생성 (#{enc.id})"
    _notify(from_user, msg)

    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


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
    req.status = IntroRequestStatus.DECLINED
    req.response_note = response_note.strip()
    req.updated_at = datetime.utcnow()
    session.add(req)
    session.commit()

    from_user = session.get(User, req.from_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"❌ <b>소개 요청 거절됨</b>\n"
        f"<b>By:</b> {current_user.email}\n"
        f"<b>매칭:</b> {_person_summary(my_p)} × {_person_summary(their_p)}\n"
    )
    if response_note:
        msg += f"<b>사유:</b> {response_note}\n"
    _notify(from_user, msg)

    return RedirectResponse("/requests", status_code=303)


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
    session.commit()

    to_user = session.get(User, req.to_user_id)
    my_p = session.get(Person, req.my_person_id)
    their_p = session.get(Person, req.their_person_id)
    msg = (
        f"↩️ <b>소개 요청 취소됨</b>\n"
        f"<b>By:</b> {current_user.email}\n"
        f"<b>매칭:</b> {_person_summary(my_p)} × {_person_summary(their_p)}\n"
    )
    _notify(to_user, msg)

    return RedirectResponse("/requests", status_code=303)
