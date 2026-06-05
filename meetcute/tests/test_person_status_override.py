"""매물 상태 수동 오버라이드 (Person.status_override)."""
import pytest


def _register(client, email):
    client.cookies.clear()
    client.post(
        "/auth/register",
        data={"email": email, "password": "pw12345678", "password_confirm": "pw12345678"},
        follow_redirects=False,
    )


def _login(client, email):
    client.cookies.clear()
    client.post(
        "/auth/login",
        data={"email": email, "password": "pw12345678"},
        follow_redirects=False,
    )


@pytest.fixture
def owner_with_person(client, session):
    """boss(=책임자) 가 매물 하나 등록."""
    from app.auth import find_user_by_email
    from app.models import Person
    from sqlmodel import select

    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    client.post(
        "/persons",
        data={
            "gender": "F", "birth_year": "97", "location": "서울",
            "workplace": "회사", "height_cm": "165",
            "owner_user_id": str(boss.id),
        },
        follow_redirects=False,
    )
    p = session.exec(select(Person)).first()
    return {"boss_id": boss.id, "person": p}


def test_override_to_unavailable(client, session, owner_with_person):
    from app.models import Person, PersonStatus
    from app.services.status import status_for_person

    p_id = owner_with_person["person"].id
    r = client.post(f"/persons/{p_id}/status",
                    data={"status": "UNAVAILABLE"}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    p = session.get(Person, p_id)
    assert p.status_override == PersonStatus.UNAVAILABLE
    assert status_for_person(session, p_id) == PersonStatus.UNAVAILABLE


def test_override_auto_clears(client, session, owner_with_person):
    from app.models import Person, PersonStatus

    p_id = owner_with_person["person"].id
    client.post(f"/persons/{p_id}/status", data={"status": "MATCHED"}, follow_redirects=False)
    session.expire_all()
    assert session.get(Person, p_id).status_override == PersonStatus.MATCHED

    # 'auto' → 오버라이드 해제
    r = client.post(f"/persons/{p_id}/status", data={"status": "auto"}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Person, p_id).status_override is None


def test_override_beats_derive(client, session, owner_with_person):
    """매물에 active Encounter 가 있어 derive=IN_PROGRESS 여도, 오버라이드가 우선."""
    from datetime import date
    from app.models import Encounter, EncounterOutcome, Person, PersonStatus
    from app.services.status import effective_status, encounters_for_person

    p = owner_with_person["person"]
    enc = Encounter(
        person_a_id=p.id, person_b_id=None,
        person_a_snapshot=p.public_id, person_b_snapshot="X-001",
        met_on=date.today(), outcome=EncounterOutcome.PENDING,
    )
    session.add(enc); session.commit()

    # derive 만 보면 IN_PROGRESS
    encs = encounters_for_person(session, p.id)
    assert effective_status(p, encs) == PersonStatus.IN_PROGRESS

    # 오버라이드 = AVAILABLE → effective 도 AVAILABLE
    client.post(f"/persons/{p.id}/status", data={"status": "AVAILABLE"}, follow_redirects=False)
    session.expire_all()
    p_fresh = session.get(Person, p.id)
    assert effective_status(p_fresh, encs) == PersonStatus.AVAILABLE


def test_invalid_status_400(client, owner_with_person):
    p_id = owner_with_person["person"].id
    r = client.post(f"/persons/{p_id}/status", data={"status": "BANANA"}, follow_redirects=False)
    assert r.status_code == 400


def test_non_owner_cannot_override(client, session, owner_with_person):
    """다른 마담뚜(책임자 아님)는 남의 매물 상태 못 바꿈 — 403 또는 redirect."""
    from app.auth import find_user_by_email

    _register(client, "rando@x.com")
    # boss 가 rando 를 마담뚜로 승급 (그래야 require_admin 통과)
    _login(client, "boss@x.com")
    rando = find_user_by_email(session, "rando@x.com")
    client.post(f"/users/{rando.id}/toggle-admin", follow_redirects=False)

    _login(client, "rando@x.com")
    p_id = owner_with_person["person"].id
    r = client.post(f"/persons/{p_id}/status",
                    data={"status": "UNAVAILABLE"}, follow_redirects=False)
    # _require_edit 가 권한 부족이면 HTTPException 발생 → 403 또는 redirect
    assert r.status_code in (303, 403)
    # 상태 안 바뀜
    from app.models import Person
    session.expire_all()
    assert session.get(Person, p_id).status_override is None
