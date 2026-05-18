"""두 매물 빠른 비교 페이지."""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select, or_, and_

from ..database import get_session
from ..models import Encounter, Person
from ..routers.blacklist import is_blacklisted
from ..services.status import (
    derive_status,
    encounters_for_person,
    status_badge_class,
    status_label,
)
from ..templating import templates

router = APIRouter(prefix="/compatibility", tags=["compatibility"])


def _shared_encounters(session: Session, a_id: int, b_id: int) -> list[Encounter]:
    stmt = select(Encounter).where(
        or_(
            and_(Encounter.person_a_id == a_id, Encounter.person_b_id == b_id),
            and_(Encounter.person_a_id == b_id, Encounter.person_b_id == a_id),
        )
    ).order_by(Encounter.met_on.desc())
    return session.exec(stmt).all()


@router.get("", response_class=HTMLResponse)
def compatibility(
    request: Request,
    a: Optional[int] = None,
    b: Optional[int] = None,
    session: Session = Depends(get_session),
):
    all_persons = session.exec(select(Person).order_by(Person.public_id)).all()
    person_a = session.get(Person, a) if a else None
    person_b = session.get(Person, b) if b else None

    shared: list[Encounter] = []
    a_status = b_status = None
    notes: list[dict] = []  # [{"level": "ok"|"warn"|"info", "text": "..."}]

    if person_a and person_b:
        if person_a.id == person_b.id:
            notes.append({"level": "warn", "text": "같은 매물입니다."})
        shared = _shared_encounters(session, person_a.id, person_b.id)
        a_status = derive_status(encounters_for_person(session, person_a.id))
        b_status = derive_status(encounters_for_person(session, person_b.id))

        if is_blacklisted(session, person_a.id, person_b.id):
            notes.append({
                "level": "warn",
                "text": "🚫 블랙리스트 페어 — 절대 매칭 금지로 마킹됨.",
            })

        if shared:
            notes.append({
                "level": "warn",
                "text": f"이미 {len(shared)}회 만난 기록이 있습니다.",
            })
        else:
            notes.append({"level": "ok", "text": "이전 만남 기록 없음."})

        if person_a.gender == person_b.gender:
            notes.append({
                "level": "info",
                "text": f"같은 성별({person_a.gender.value}) — 동성 매칭 의도인지 확인.",
            })

        # 출생연도 차이 (같은 세기 가정). 50 보다 차이가 크면 세기 다른 경우라 100 빼서 보정.
        yr_diff = abs(person_a.birth_year - person_b.birth_year)
        if yr_diff > 50:
            yr_diff = 100 - yr_diff
        notes.append({
            "level": "info" if yr_diff <= 5 else "warn",
            "text": f"출생연도 차이 {yr_diff}년.",
        })

        height_diff = abs(person_a.height_cm - person_b.height_cm)
        notes.append({
            "level": "info",
            "text": f"키 차이 {height_diff}cm.",
        })

    return templates.TemplateResponse(
        request,
        "compatibility.html",
        {
            "all_persons": all_persons,
            "person_a": person_a,
            "person_b": person_b,
            "shared": shared,
            "notes": notes,
            "a_status": a_status,
            "b_status": b_status,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
        },
    )
