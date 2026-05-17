"""Person 상태는 DB에 저장하지 않고 Encounter들로부터 파생한다.

규칙:
  - MATCHED 결과가 하나라도 있으면 → MATCHED
  - 그 외에 PENDING/CONTINUING(=is_active) 결과가 하나라도 있으면 → IN_PROGRESS
  - 모두 종료(ENDED_*/MUTUAL_END)거나 만남 기록이 없으면 → AVAILABLE
"""
from enum import Enum
from sqlmodel import Session, select, or_
from ..models import Encounter, EncounterOutcome, Person


class PersonStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    IN_PROGRESS = "IN_PROGRESS"
    MATCHED = "MATCHED"


_LABEL = {
    PersonStatus.AVAILABLE: "소개 가능",
    PersonStatus.IN_PROGRESS: "진행 중",
    PersonStatus.MATCHED: "매칭됨",
}

_BADGE_CLASS = {
    PersonStatus.AVAILABLE: "bg-emerald-100 text-emerald-700 border-emerald-200",
    PersonStatus.IN_PROGRESS: "bg-amber-100 text-amber-700 border-amber-200",
    PersonStatus.MATCHED: "bg-pink-100 text-pink-700 border-pink-200",
}


def status_label(s: PersonStatus) -> str:
    return _LABEL[s]


def status_badge_class(s: PersonStatus) -> str:
    return _BADGE_CLASS[s]


def encounters_for_person(session: Session, person_id: int) -> list[Encounter]:
    stmt = (
        select(Encounter)
        .where(or_(Encounter.person_a_id == person_id, Encounter.person_b_id == person_id))
        .order_by(Encounter.met_on.desc(), Encounter.id.desc())
    )
    return session.exec(stmt).all()


def derive_status(encounters: list[Encounter]) -> PersonStatus:
    has_active = False
    for e in encounters:
        if e.outcome == EncounterOutcome.MATCHED:
            return PersonStatus.MATCHED
        if e.outcome.is_active:
            has_active = True
    return PersonStatus.IN_PROGRESS if has_active else PersonStatus.AVAILABLE


def status_for_person(session: Session, person_id: int) -> PersonStatus:
    return derive_status(encounters_for_person(session, person_id))


def statuses_for_persons(
    session: Session, persons: list[Person]
) -> dict[int, PersonStatus]:
    """N+1 회피: 한 번에 모든 관련 Encounter 가져와서 그룹핑."""
    if not persons:
        return {}
    ids = [p.id for p in persons if p.id is not None]
    stmt = select(Encounter).where(
        or_(Encounter.person_a_id.in_(ids), Encounter.person_b_id.in_(ids))
    )
    grouped: dict[int, list[Encounter]] = {pid: [] for pid in ids}
    for e in session.exec(stmt).all():
        if e.person_a_id in grouped:
            grouped[e.person_a_id].append(e)
        if e.person_b_id in grouped:
            grouped[e.person_b_id].append(e)
    return {pid: derive_status(grouped[pid]) for pid in ids}
