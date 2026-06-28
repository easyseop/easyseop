"""소개팅/매칭 통계 집계 — 저장 안 하고 매 요청 때 계산 (데이터 규모 작음).

용어:
  - 결정된 만남(decided): MATCHED/ENDED_A/ENDED_B/MUTUAL_END (결과가 확정됨)
  - 진행 중(active): PENDING/CONTINUING (아직 결과 미정)
  - 커플 성사율: MATCHED / 결정된 만남  (진행 중은 분모에서 제외 — 아직 모르니까)
  - 전체 매칭률: MATCHED / 전체 만남
  - 요청 수락률: ACCEPTED / (ACCEPTED + DECLINED)  (대기/취소 제외)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from ..models import (
    Encounter,
    EncounterOutcome,
    IntroductionRequest,
    IntroRequestStatus,
    Person,
    User,
)

DECIDED_OUTCOMES = {
    EncounterOutcome.MATCHED,
    EncounterOutcome.ENDED_A,
    EncounterOutcome.ENDED_B,
    EncounterOutcome.MUTUAL_END,
}
ACTIVE_OUTCOMES = {EncounterOutcome.PENDING, EncounterOutcome.CONTINUING}


def _rate(numer: int, denom: int) -> float:
    return round(100.0 * numer / denom, 1) if denom else 0.0


@dataclass
class MatchmakerStat:
    user_id: int
    name: str
    persons: int = 0
    encounters: int = 0
    matched: int = 0
    requests_sent: int = 0
    requests_accepted: int = 0
    requests_declined: int = 0

    @property
    def match_rate(self) -> float:
        return _rate(self.matched, self.encounters)

    @property
    def accept_rate(self) -> float:
        return _rate(self.requests_accepted, self.requests_accepted + self.requests_declined)


def compute_stats(session: Session) -> dict:
    encounters = session.exec(select(Encounter)).all()
    requests = session.exec(select(IntroductionRequest)).all()
    persons = session.exec(select(Person)).all()
    users = session.exec(
        select(User).where(User.is_admin == True)  # noqa: E712
    ).all()

    # ── 만남 결과 분포 ────────────────────────────────────────────
    outcome_counts = {o: 0 for o in EncounterOutcome}
    for e in encounters:
        outcome_counts[e.outcome] = outcome_counts.get(e.outcome, 0) + 1

    total_enc = len(encounters)
    matched = outcome_counts[EncounterOutcome.MATCHED]
    decided = sum(outcome_counts[o] for o in DECIDED_OUTCOMES)
    active = sum(outcome_counts[o] for o in ACTIVE_OUTCOMES)

    # ── 소개 요청 퍼널 ────────────────────────────────────────────
    req_counts = {s: 0 for s in IntroRequestStatus}
    for r in requests:
        req_counts[r.status] = req_counts.get(r.status, 0) + 1
    req_total = len(requests)
    req_accepted = req_counts[IntroRequestStatus.ACCEPTED]
    req_declined = req_counts[IntroRequestStatus.DECLINED]

    # ── 월별 추이 (최근 6개월, met_on 기준) ──────────────────────
    by_month: dict[str, dict] = defaultdict(lambda: {"enc": 0, "matched": 0})
    for e in encounters:
        if not e.met_on:
            continue
        key = e.met_on.strftime("%Y-%m")
        by_month[key]["enc"] += 1
        if e.outcome == EncounterOutcome.MATCHED:
            by_month[key]["matched"] += 1
    months_sorted = sorted(by_month.keys())[-6:]
    monthly = [
        {"month": m, "enc": by_month[m]["enc"], "matched": by_month[m]["matched"]}
        for m in months_sorted
    ]
    monthly_max = max((m["enc"] for m in monthly), default=0)

    # ── 마담뚜별 성과 ────────────────────────────────────────────
    owner_of = {p.id: p.owner_user_id for p in persons}
    stat_by_user: dict[int, MatchmakerStat] = {
        u.id: MatchmakerStat(user_id=u.id, name=u.display_name) for u in users
    }
    for p in persons:
        st = stat_by_user.get(p.owner_user_id)
        if st:
            st.persons += 1
    for e in encounters:
        # 양쪽 매물 owner 각각에 카운트 (중복 owner 면 한 번만)
        owners = set()
        for pid in (e.person_a_id, e.person_b_id):
            oid = owner_of.get(pid) if pid else None
            if oid:
                owners.add(oid)
        for oid in owners:
            st = stat_by_user.get(oid)
            if not st:
                continue
            st.encounters += 1
            if e.outcome == EncounterOutcome.MATCHED:
                st.matched += 1
    for r in requests:
        st = stat_by_user.get(r.from_user_id)
        if not st:
            continue
        st.requests_sent += 1
        if r.status == IntroRequestStatus.ACCEPTED:
            st.requests_accepted += 1
        elif r.status == IntroRequestStatus.DECLINED:
            st.requests_declined += 1

    matchmaker_stats = sorted(
        stat_by_user.values(),
        key=lambda s: (s.matched, s.encounters),
        reverse=True,
    )

    return {
        # 핵심 지표
        "total_encounters": total_enc,
        "matched": matched,
        "decided": decided,
        "active": active,
        "couple_rate": _rate(matched, decided),     # 결정된 만남 중 매칭 %
        "overall_match_rate": _rate(matched, total_enc),
        # 요청 퍼널
        "req_total": req_total,
        "req_counts": req_counts,
        "req_accepted": req_accepted,
        "req_declined": req_declined,
        "accept_rate": _rate(req_accepted, req_accepted + req_declined),
        # 분포
        "outcome_counts": outcome_counts,
        # 추이
        "monthly": monthly,
        "monthly_max": monthly_max,
        # 마담뚜별
        "matchmaker_stats": matchmaker_stats,
    }
