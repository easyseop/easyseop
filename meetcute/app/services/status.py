"""Person 상태는 기본적으로 Encounter 들로부터 파생(derive)한다.
담당자가 명시적으로 오버라이드(Person.status_override) 한 경우 그 값을 우선 사용.

기본 파생 규칙:
  - MATCHED 결과가 하나라도 있으면 → MATCHED
  - 그 외에 PENDING/CONTINUING(=is_active) 결과가 하나라도 있으면 → IN_PROGRESS
  - 모두 종료(ENDED_*/MUTUAL_END)거나 만남 기록이 없으면 → AVAILABLE

UNAVAILABLE("불가") 는 오버라이드 전용 — 매물이 일시적/영구적으로 소개 불가
(예: 휴식기, 본인 사정) 일 때 담당자가 수동 지정.
"""
from typing import Optional
from sqlmodel import Session, select, or_
from sqlalchemy.orm import defer
from ..models import Encounter, EncounterOutcome, Person, PersonStatus  # re-exported below


__all__ = [
    "PersonStatus", "status_label", "status_badge_class",
    "derive_status", "effective_status", "status_for_person",
    "encounters_for_person", "grouped_encounters_for_persons",
    "statuses_for_persons",
]


_LABEL = {
    PersonStatus.AVAILABLE: "소개 가능",
    PersonStatus.IN_PROGRESS: "진행 중",
    PersonStatus.MATCHED: "매칭됨",
    PersonStatus.UNAVAILABLE: "불가",
}

_BADGE_CLASS = {
    PersonStatus.AVAILABLE: "bg-emerald-100 text-emerald-700 border-emerald-200",
    PersonStatus.IN_PROGRESS: "bg-amber-100 text-amber-700 border-amber-200",
    PersonStatus.MATCHED: "bg-pink-100 text-pink-700 border-pink-200",
    PersonStatus.UNAVAILABLE: "bg-neutral-200 text-neutral-700 border-neutral-300",
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
    """encounters 만으로 자동 계산 — Person.status_override 는 무시."""
    has_active = False
    for e in encounters:
        if e.outcome == EncounterOutcome.MATCHED:
            return PersonStatus.MATCHED
        if e.outcome.is_active:
            has_active = True
    return PersonStatus.IN_PROGRESS if has_active else PersonStatus.AVAILABLE


def effective_status(
    person: Optional[Person], encounters: list[Encounter]
) -> PersonStatus:
    """오버라이드가 있으면 그것, 없으면 encounters 로부터 derive."""
    if person is not None and getattr(person, "status_override", None):
        return person.status_override
    return derive_status(encounters)


def status_for_person(session: Session, person_id: int) -> PersonStatus:
    """오버라이드까지 반영한 effective_status (단건)."""
    person = session.get(Person, person_id)
    encs = encounters_for_person(session, person_id)
    return effective_status(person, encs)


def grouped_encounters_for_persons(
    session: Session, persons: list[Person]
) -> dict[int, list[Encounter]]:
    """N+1 회피: 한 번에 모든 관련 Encounter 가져와서 person_id 별로 그룹.
    notes 컬럼은 암호화돼 있고 status/activity 계산엔 안 쓰니 defer."""
    if not persons:
        return {}
    ids = [p.id for p in persons if p.id is not None]
    stmt = (
        select(Encounter)
        .options(defer(Encounter.notes))
        .where(or_(Encounter.person_a_id.in_(ids), Encounter.person_b_id.in_(ids)))
    )
    grouped: dict[int, list[Encounter]] = {pid: [] for pid in ids}
    for e in session.exec(stmt).all():
        if e.person_a_id in grouped:
            grouped[e.person_a_id].append(e)
        if e.person_b_id in grouped:
            grouped[e.person_b_id].append(e)
    return grouped


def statuses_for_persons(
    session: Session,
    persons: list[Person],
    grouped: Optional[dict[int, list[Encounter]]] = None,
) -> dict[int, PersonStatus]:
    """오버라이드 우선 + encounter 파생을 반영한 effective status 배치.
    grouped 가 주어지면 재쿼리 안 함."""
    if not persons:
        return {}
    if grouped is None:
        grouped = grouped_encounters_for_persons(session, persons)
    person_by_id = {p.id: p for p in persons if p.id is not None}
    return {
        pid: effective_status(person_by_id.get(pid), encs)
        for pid, encs in grouped.items()
    }
