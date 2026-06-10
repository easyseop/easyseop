from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select, or_
from sqlalchemy.orm import defer

from ..auth import get_current_user
from ..config import AUTH_ENABLED
from ..database import get_session
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
from ..services.activity_log import log_activity
from ..services.visibility import allowed_set_for_user, can_see_person
from ..templating import templates


def _visible_map(
    session: Session, persons: dict[int, Person], user: Optional[User]
) -> dict[int, bool]:
    """persons dict 의 각 id 가 user 에게 보이는지 batch 로 계산.
    RESTRICTED 매물의 PersonAllowedAdmin 도 한 번에 로드해서 N+1 회피."""
    if not persons:
        return {}
    allowed = allowed_set_for_user(session, user)
    return {
        pid: can_see_person(p, user, allowed_set=allowed)
        for pid, p in persons.items()
    }

router = APIRouter(prefix="/encounters", tags=["encounters"])


OUTCOME_LABEL = {
    EncounterOutcome.PENDING: "예정/결과 미정",
    EncounterOutcome.CONTINUING: "계속 만나는 중",
    EncounterOutcome.MATCHED: "매칭 성공 💘",
    EncounterOutcome.ENDED_A: "A가 거절",
    EncounterOutcome.ENDED_B: "B가 거절",
    EncounterOutcome.MUTUAL_END: "양쪽 다 노",
}

OUTCOME_BADGE = {
    EncounterOutcome.PENDING: "bg-neutral-100 text-neutral-700",
    EncounterOutcome.CONTINUING: "bg-amber-100 text-amber-700",
    EncounterOutcome.MATCHED: "bg-pink-100 text-pink-700",
    EncounterOutcome.ENDED_A: "bg-neutral-100 text-neutral-500",
    EncounterOutcome.ENDED_B: "bg-neutral-100 text-neutral-500",
    EncounterOutcome.MUTUAL_END: "bg-neutral-100 text-neutral-500",
}


def _ctx_extras() -> dict:
    return {
        "OUTCOME_LABEL": OUTCOME_LABEL,
        "OUTCOME_BADGE": OUTCOME_BADGE,
        "EncounterOutcome": EncounterOutcome,
    }


def _resolve_persons(session: Session, encounters: list[Encounter]) -> dict[int, Person]:
    """Encounter 목록에서 등장하는 person_id들을 한 번에 조회."""
    ids = set()
    for e in encounters:
        if e.person_a_id:
            ids.add(e.person_a_id)
        if e.person_b_id:
            ids.add(e.person_b_id)
    if not ids:
        return {}
    rows = session.exec(select(Person).where(Person.id.in_(list(ids)))).all()
    return {p.id: p for p in rows}


def _log_event(
    session: Session,
    enc: Encounter,
    outcome: EncounterOutcome,
    note: str,
    actor: Optional[User],
) -> EncounterEvent:
    ev = EncounterEvent(
        encounter_id=enc.id,
        outcome=outcome,
        note=note,
        changed_by_user_id=actor.id if actor else None,
        # 표시용. 컬럼명은 _email 유지하되 실제로는 display_name 저장 (이메일 노출 방지)
        changed_by_email=actor.display_name if actor else "",
    )
    session.add(ev)
    return ev


def _events_for(session: Session, encounter_id: int) -> list[EncounterEvent]:
    return session.exec(
        select(EncounterEvent)
        .where(EncounterEvent.encounter_id == encounter_id)
        .order_by(EncounterEvent.created_at.desc())
    ).all()


@router.get("", response_class=HTMLResponse)
def list_encounters(
    request: Request,
    person_id: Optional[int] = None,
    outcome: Optional[str] = None,  # "" 허용 (enum 검증 우회)
    session: Session = Depends(get_session),
):
    outcome_enum: Optional[EncounterOutcome] = None
    if outcome:
        try:
            outcome_enum = EncounterOutcome(outcome)
        except ValueError:
            outcome_enum = None

    # 목록 view 에선 notes 안 보여줌 → defer
    stmt = (
        select(Encounter)
        .options(defer(Encounter.notes))
        .order_by(Encounter.met_on.desc(), Encounter.id.desc())
    )
    if person_id:
        stmt = stmt.where(
            or_(Encounter.person_a_id == person_id, Encounter.person_b_id == person_id)
        )
    if outcome_enum:
        stmt = stmt.where(Encounter.outcome == outcome_enum)
    encounters = session.exec(stmt).all()
    persons = _resolve_persons(session, encounters)
    current_user = get_current_user(request, session)
    visible = _visible_map(session, persons, current_user)

    # 연락처 전달 대기 (양방 동의됐지만 아직 Encounter 안 만들어진 요청들).
    # 현재 user 가 참여한 것만 — 본인이 참여자라 visibility 무관 (요청 양 owner).
    awaiting_contact = []
    awaiting_persons: dict[int, Person] = {}
    if AUTH_ENABLED:
        if current_user and current_user.id:
            awaiting_contact = session.exec(
                select(IntroductionRequest).where(
                    IntroductionRequest.status == IntroRequestStatus.PENDING,
                    IntroductionRequest.sender_own_consent == SenderConsentStatus.AGREED,
                    IntroductionRequest.receiver_own_consent == SenderConsentStatus.AGREED,
                    or_(
                        IntroductionRequest.from_user_id == current_user.id,
                        IntroductionRequest.to_user_id == current_user.id,
                    ),
                ).order_by(IntroductionRequest.updated_at.desc())
            ).all()
            if awaiting_contact:
                pids = set()
                for r in awaiting_contact:
                    pids.add(r.my_person_id); pids.add(r.their_person_id)
                rows = session.exec(select(Person).where(Person.id.in_(list(pids)))).all()
                awaiting_persons = {p.id: p for p in rows}

    return templates.TemplateResponse(
        request,
        "encounters/list.html",
        {
            "encounters": encounters,
            "persons": persons,
            "visible": visible,
            "person_id": person_id,
            "awaiting_contact": awaiting_contact,
            "awaiting_persons": awaiting_persons,
            "outcome": outcome_enum,
            **_ctx_extras(),
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_encounter_form(
    request: Request,
    a: Optional[int] = None,
    b: Optional[int] = None,
    session: Session = Depends(get_session),
):
    # 비공개 매물은 권한 없는 마담뚜에겐 드롭다운에서 숨김
    current_user = get_current_user(request, session)
    allowed = allowed_set_for_user(session, current_user)
    rows = session.exec(select(Person).order_by(Person.public_id)).all()
    all_persons = [p for p in rows if can_see_person(p, current_user, allowed_set=allowed)]
    return templates.TemplateResponse(
        request,
        "encounters/new.html",
        {
            "all_persons": all_persons,
            "preset_a": a,
            "preset_b": b,
            "today": date.today().isoformat(),
            **_ctx_extras(),
        },
    )


@router.post("")
def create_encounter(
    request: Request,
    person_a_id: int = Form(...),
    person_b_id: int = Form(...),
    met_on: date = Form(...),
    outcome: EncounterOutcome = Form(EncounterOutcome.PENDING),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    if person_a_id == person_b_id:
        raise HTTPException(400, "같은 매물끼리는 만남 기록을 만들 수 없어요")
    a = session.get(Person, person_a_id)
    b = session.get(Person, person_b_id)
    if not a or not b:
        raise HTTPException(404, "매물을 찾을 수 없습니다")

    enc = Encounter(
        person_a_id=a.id,
        person_b_id=b.id,
        person_a_snapshot=a.public_id,
        person_b_snapshot=b.public_id,
        met_on=met_on,
        outcome=outcome,
        notes=notes,
    )
    session.add(enc)
    session.commit()
    session.refresh(enc)

    # 초기 outcome 이벤트 기록
    actor = get_current_user(request, session)
    _log_event(session, enc, outcome, "최초 등록", actor)
    log_activity(
        session, actor, "encounter.create",
        target_type="encounter", target_id=enc.id,
        summary=f"{a.public_id} × {b.public_id} 만남 기록",
    )
    session.commit()

    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


@router.get("/{encounter_id}", response_class=HTMLResponse)
def encounter_detail(
    encounter_id: int, request: Request, session: Session = Depends(get_session)
):
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")
    persons = _resolve_persons(session, [enc])
    current_user = get_current_user(request, session)
    visible = _visible_map(session, persons, current_user)
    events = _events_for(session, enc.id)
    return templates.TemplateResponse(
        request,
        "encounters/edit.html",
        {"enc": enc, "persons": persons, "visible": visible,
         "events": events, **_ctx_extras()},
    )


@router.post("/{encounter_id}")
def update_encounter(
    request: Request,
    encounter_id: int,
    met_on: date = Form(...),
    outcome: EncounterOutcome = Form(...),
    notes: str = Form(""),
    transition_note: str = Form(""),
    session: Session = Depends(get_session),
):
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")

    outcome_changed = enc.outcome != outcome
    enc.met_on = met_on
    enc.outcome = outcome
    enc.notes = notes
    enc.updated_at = datetime.utcnow()
    session.add(enc)

    if outcome_changed:
        actor = get_current_user(request, session)
        _log_event(session, enc, outcome, transition_note.strip(), actor)
        log_activity(
            session, actor, "encounter.update",
            target_type="encounter", target_id=enc.id,
            summary=f"#{enc.id} 상태: {OUTCOME_LABEL.get(outcome, outcome.value)}",
        )

    session.commit()
    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


@router.post("/{encounter_id}/delete")
def delete_encounter(
    encounter_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    """Encounter 자체는 보통 보존하지만, 잘못 입력했을 때 삭제용.
    Encounter를 지우면 그 만남의 outcome 변화 이력(EncounterEvent)도 같이 사라짐.
    """
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")

    # 연관 이벤트 정리
    events = session.exec(
        select(EncounterEvent).where(EncounterEvent.encounter_id == enc.id)
    ).all()
    for ev in events:
        session.delete(ev)

    actor = get_current_user(request, session)
    enc_snap = f"#{enc.id} ({enc.person_a_snapshot} × {enc.person_b_snapshot})"
    session.delete(enc)
    log_activity(
        session, actor, "encounter.delete",
        target_type="encounter", target_id=encounter_id,
        summary=f"{enc_snap} 삭제",
    )
    session.commit()
    return RedirectResponse("/encounters", status_code=303)
