"""크로스-마담뚜 IntroductionRequest 흐름: 보내기 → 수락 → Encounter 자동 생성."""
import pytest


def _register(client, email):
    client.cookies.clear()
    client.post(
        "/auth/register",
        data={
            "email": email, "password": "pw12345678",
            "password_confirm": "pw12345678",
        },
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
def two_admins(client, session):
    """boss(책임자) 가 noob 을 자동 승급 → 둘 다 마담뚜."""
    from app.models import User
    from sqlmodel import select

    _register(client, "boss@x.com")
    _register(client, "noob@x.com")

    _login(client, "boss@x.com")
    # noob 을 admin 으로 승급
    noob = session.exec(select(User).where(User.id != 1)).first()
    r = client.post(f"/users/{noob.id}/toggle-admin", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    noob = session.get(User, noob.id)
    assert noob.is_admin is True

    # boss=책임자, noob=일반 마담뚜. 각자 매물 하나씩 (owner_user_id 명시 — 폼이 보내는 값)
    boss_id = 1
    noob_id = noob.id
    _login(client, "boss@x.com")
    client.post(
        "/persons",
        data={
            "gender": "M", "age": "30", "location": "서울",
            "workplace": "회사", "height_cm": "178",
            "owner_user_id": str(boss_id),
        },
        follow_redirects=False,
    )
    _login(client, "noob@x.com")
    client.post(
        "/persons",
        data={
            "gender": "F", "age": "28", "location": "서울",
            "workplace": "회사", "height_cm": "165",
            "owner_user_id": str(noob_id),
        },
        follow_redirects=False,
    )
    return {"boss_id": boss_id, "noob_id": noob_id}


def test_request_accept_creates_encounter(client, session, two_admins):
    """boss(M 매물) → noob(F 매물) 소개 요청 → noob 수락 → Encounter 자동 생성."""
    from app.models import (
        Encounter, EncounterOutcome, IntroductionRequest,
        IntroRequestStatus, Person,
    )
    from sqlmodel import select

    persons = session.exec(select(Person).order_by(Person.id)).all()
    boss_person = next(p for p in persons if p.owner_user_id == two_admins["boss_id"])
    noob_person = next(p for p in persons if p.owner_user_id == two_admins["noob_id"])

    # boss 가 본인 매물을 noob 매물에 소개 요청
    _login(client, "boss@x.com")
    r = client.post(
        "/requests",
        data={
            "my_person_id": str(boss_person.id),
            "their_person_id": str(noob_person.id),
            "message": "잘 어울릴 거 같아요",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    reqs = session.exec(select(IntroductionRequest)).all()
    assert len(reqs) == 1
    req = reqs[0]
    assert req.status == IntroRequestStatus.PENDING
    assert req.from_user_id == two_admins["boss_id"]
    assert req.to_user_id == two_admins["noob_id"]

    # noob 가 수락
    _login(client, "noob@x.com")
    r = client.post(
        f"/requests/{req.id}/accept",
        data={"response_note": "좋아요"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/encounters/")

    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.ACCEPTED
    assert req.resolved_encounter_id is not None

    enc = session.get(Encounter, req.resolved_encounter_id)
    assert enc is not None
    assert enc.outcome == EncounterOutcome.PENDING
    assert {enc.person_a_id, enc.person_b_id} == {boss_person.id, noob_person.id}


def test_request_decline(client, session, two_admins):
    """거절: status 만 DECLINED 로 바뀌고 Encounter 안 만들어짐."""
    from app.models import (
        Encounter, IntroductionRequest, IntroRequestStatus, Person,
    )
    from sqlmodel import select

    persons = session.exec(select(Person).order_by(Person.id)).all()
    boss_person = next(p for p in persons if p.owner_user_id == two_admins["boss_id"])
    noob_person = next(p for p in persons if p.owner_user_id == two_admins["noob_id"])

    _login(client, "boss@x.com")
    client.post(
        "/requests",
        data={
            "my_person_id": str(boss_person.id),
            "their_person_id": str(noob_person.id),
            "message": "",
        },
        follow_redirects=False,
    )
    req = session.exec(select(IntroductionRequest)).first()

    _login(client, "noob@x.com")
    r = client.post(
        f"/requests/{req.id}/decline",
        data={"response_note": "타이밍이 안 맞아요"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.DECLINED
    assert req.response_note == "타이밍이 안 맞아요"
    assert req.resolved_encounter_id is None
    encs = session.exec(select(Encounter)).all()
    assert encs == []
