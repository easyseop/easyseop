"""매물 가시성(공개 범위) 권한 체크.

Person.visibility = PUBLIC / RESTRICTED.
RESTRICTED 매물은 owner + 책임자(is_owner) + 명시된 admin (PersonAllowedAdmin) 만 봄.
PUBLIC 은 모든 admin. AUTH=off 면 항상 허용.

services 로 추출한 이유: routers/encounters.py 가 매물 권한 검사를 해야 하는데
routers/persons.py 는 encounters 의 라벨/뱃지 맵을 import 해서 순환 의존 발생.
서비스 레이어로 빼서 양쪽이 깔끔하게 의존.
"""
from typing import Optional

from sqlmodel import Session, select

from ..config import AUTH_ENABLED
from ..models import Person, PersonAllowedAdmin, PersonVisibility, User


def can_see_person(
    person: Person,
    user: Optional[User],
    allowed_set: Optional[set[int]] = None,
    session: Optional[Session] = None,
) -> bool:
    """RESTRICTED 매물은 owner + 책임자 + 허용된 admin 만 볼 수 있음.
    PUBLIC 은 모든 admin. AUTH=off 면 항상 허용.

    allowed_set: 호출자가 미리 (PersonAllowedAdmin) 를 batch 로 캐싱한 경우 전달.
    session: allowed_set 미전달 시 DB 조회용."""
    if not AUTH_ENABLED:
        return True
    if not user or not user.id:
        return False
    if user.is_owner:
        return True
    if person.owner_user_id == user.id:
        return True
    if person.visibility == PersonVisibility.PUBLIC:
        return True
    # RESTRICTED
    if allowed_set is not None:
        return person.id in allowed_set
    if session is None:
        return False
    paa = session.exec(
        select(PersonAllowedAdmin).where(
            PersonAllowedAdmin.person_id == person.id,
            PersonAllowedAdmin.user_id == user.id,
        )
    ).first()
    return paa is not None


def allowed_set_for_user(session: Session, user: Optional[User]) -> set[int]:
    """user 에게 명시적으로 허용된 RESTRICTED 매물 id 집합. 목록 view 에서
    can_see_person 호출 N+1 방지용."""
    if not user or not user.id:
        return set()
    rows = session.exec(
        select(PersonAllowedAdmin.person_id).where(
            PersonAllowedAdmin.user_id == user.id
        )
    ).all()
    return set(rows)
