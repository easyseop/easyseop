from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select, or_

from ..database import get_session
from ..models import Encounter, EncounterOutcome, Person
from ..templating import templates

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


@router.get("", response_class=HTMLResponse)
def list_encounters(
    request: Request,
    person_id: Optional[int] = None,
    outcome: Optional[EncounterOutcome] = None,
    session: Session = Depends(get_session),
):
    stmt = select(Encounter).order_by(Encounter.met_on.desc(), Encounter.id.desc())
    if person_id:
        stmt = stmt.where(
            or_(Encounter.person_a_id == person_id, Encounter.person_b_id == person_id)
        )
    if outcome:
        stmt = stmt.where(Encounter.outcome == outcome)
    encounters = session.exec(stmt).all()
    persons = _resolve_persons(session, encounters)
    return templates.TemplateResponse(
        request,
        "encounters/list.html",
        {
            "encounters": encounters,
            "persons": persons,
            "person_id": person_id,
            "outcome": outcome,
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
    all_persons = session.exec(select(Person).order_by(Person.public_id)).all()
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
    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


@router.get("/{encounter_id}", response_class=HTMLResponse)
def encounter_detail(
    encounter_id: int, request: Request, session: Session = Depends(get_session)
):
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")
    persons = _resolve_persons(session, [enc])
    return templates.TemplateResponse(
        request,
        "encounters/edit.html",
        {"enc": enc, "persons": persons, **_ctx_extras()},
    )


@router.post("/{encounter_id}")
def update_encounter(
    encounter_id: int,
    met_on: date = Form(...),
    outcome: EncounterOutcome = Form(...),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")
    enc.met_on = met_on
    enc.outcome = outcome
    enc.notes = notes
    enc.updated_at = datetime.utcnow()
    session.add(enc)
    session.commit()
    return RedirectResponse(f"/encounters/{enc.id}", status_code=303)


@router.post("/{encounter_id}/delete")
def delete_encounter(encounter_id: int, session: Session = Depends(get_session)):
    """Encounter 자체는 보통 보존하지만, 잘못 입력했을 때 삭제용."""
    enc = session.get(Encounter, encounter_id)
    if not enc:
        raise HTTPException(404, "Encounter not found")
    session.delete(enc)
    session.commit()
    return RedirectResponse("/encounters", status_code=303)
