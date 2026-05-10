"""매물별 활동 통계 (이력 관리용).

- total: 총 만남 횟수
- matched: MATCHED 만남 수
- active: PENDING/CONTINUING 만남 수
- ended: 종료된 만남 수
- last_activity: 가장 최근 met_on (없으면 None)
- days_dormant: 마지막 활동 이후 경과 일수 (없으면 None — 한 번도 안 만남)
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select, or_

from ..models import Encounter, EncounterOutcome, Person


@dataclass
class ActivityStats:
    total: int = 0
    matched: int = 0
    active: int = 0
    ended: int = 0
    last_activity: Optional[date] = None

    @property
    def days_dormant(self) -> Optional[int]:
        if self.last_activity is None:
            return None
        return (date.today() - self.last_activity).days

    @property
    def never_met(self) -> bool:
        return self.total == 0


def _accumulate(encs: list[Encounter]) -> ActivityStats:
    s = ActivityStats()
    for e in encs:
        s.total += 1
        if e.outcome == EncounterOutcome.MATCHED:
            s.matched += 1
        elif e.outcome.is_active:
            s.active += 1
        else:
            s.ended += 1
        if s.last_activity is None or e.met_on > s.last_activity:
            s.last_activity = e.met_on
    return s


def activity_for_person(session: Session, person_id: int) -> ActivityStats:
    encs = session.exec(
        select(Encounter).where(
            or_(
                Encounter.person_a_id == person_id,
                Encounter.person_b_id == person_id,
            )
        )
    ).all()
    return _accumulate(encs)


def activity_for_persons(
    session: Session, persons: list[Person]
) -> dict[int, ActivityStats]:
    if not persons:
        return {}
    ids = [p.id for p in persons if p.id is not None]
    encs = session.exec(
        select(Encounter).where(
            or_(Encounter.person_a_id.in_(ids), Encounter.person_b_id.in_(ids))
        )
    ).all()
    grouped: dict[int, list[Encounter]] = {pid: [] for pid in ids}
    for e in encs:
        if e.person_a_id in grouped:
            grouped[e.person_a_id].append(e)
        if e.person_b_id in grouped:
            grouped[e.person_b_id].append(e)
    return {pid: _accumulate(grouped[pid]) for pid in ids}
