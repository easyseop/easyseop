"""매물 정보 변경 이력 헬퍼.

스냅샷 정책:
  - update_person 직전에 _현재 상태_를 PersonRevision으로 저장
  - 따라서 revisions[i].snapshot_json = "그 i번째 변경 직전의 값"
  - 가장 최근 revision은 '직전 값' = 현재 Person과 비교하면 '이번 변경에서 바뀐 것'
"""
import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import Person, PersonRevision, User

TRACKED_FIELDS = (
    "birth_year",
    "location",
    "workplace",
    "height_cm",
    "ideal_type",
    "notes",
    "alias",
)

FIELD_LABEL = {
    "birth_year": "출생연도",
    "location": "거주지",
    "workplace": "직장",
    "height_cm": "키(cm)",
    "ideal_type": "이상형",
    "notes": "주선자 메모",
    "alias": "별칭",
}


def _snapshot(person: Person) -> dict[str, Any]:
    return {f: getattr(person, f) for f in TRACKED_FIELDS}


def record_revision(
    session: Session, person: Person, user: Optional[User] = None
) -> PersonRevision:
    """update_person이 변경을 가하기 _직전에_ 호출. 호출 측이 commit 책임."""
    rev = PersonRevision(
        person_id=person.id,
        snapshot_json=json.dumps(_snapshot(person), ensure_ascii=False),
        changed_by_user_id=user.id if user else None,
        # 컬럼 이름은 _email 이지만 표시용이라 display_name 저장 (이메일 노출 방지)
        changed_by_email=user.display_name if user else "",
        changed_at=datetime.utcnow(),
    )
    session.add(rev)
    return rev


def revisions_for_person(session: Session, person_id: int) -> list[PersonRevision]:
    return session.exec(
        select(PersonRevision)
        .where(PersonRevision.person_id == person_id)
        .order_by(PersonRevision.changed_at.desc())
    ).all()


def diff_against(snapshot_json: str, current: Person) -> list[tuple[str, Any, Any]]:
    """snapshot_json(=직전 값) 과 current(=그 이후 상태) 의 차이.

    반환: [(field_label, before, after), ...] - 변경이 있는 것만.
    """
    before = json.loads(snapshot_json)
    out: list[tuple[str, Any, Any]] = []
    for f in TRACKED_FIELDS:
        b = before.get(f)
        a = getattr(current, f)
        if b != a:
            out.append((FIELD_LABEL.get(f, f), b, a))
    return out


def diff_between(older_json: str, newer_json: str) -> list[tuple[str, Any, Any]]:
    """두 revision 사이의 차이."""
    older = json.loads(older_json)
    newer = json.loads(newer_json)
    out: list[tuple[str, Any, Any]] = []
    for f in TRACKED_FIELDS:
        b = older.get(f)
        a = newer.get(f)
        if b != a:
            out.append((FIELD_LABEL.get(f, f), b, a))
    return out
