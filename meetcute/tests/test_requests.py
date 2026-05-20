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
            "gender": "M", "birth_year": "95", "location": "서울",
            "workplace": "회사", "height_cm": "178",
            "owner_user_id": str(boss_id),
        },
        follow_redirects=False,
    )
    _login(client, "noob@x.com")
    client.post(
        "/persons",
        data={
            "gender": "F", "birth_year": "97", "location": "서울",
            "workplace": "회사", "height_cm": "165",
            "owner_user_id": str(noob_id),
        },
        follow_redirects=False,
    )
    return {"boss_id": boss_id, "noob_id": noob_id}


def test_request_both_agreed_then_contact_exchange(client, session, two_admins):
    """양방 동의만으론 Encounter 안 생김 → '연락처 전달 완료' 버튼 클릭 시 생성.
    바뀐 흐름: 양방 AGREED → status PENDING 유지 → confirm-contact-exchanged → ACCEPTED + Encounter."""
    from app.models import (
        Encounter, EncounterOutcome, IntroductionRequest,
        IntroRequestStatus, Person, SenderConsentStatus,
    )
    from sqlmodel import select

    persons = session.exec(select(Person).order_by(Person.id)).all()
    boss_person = next(p for p in persons if p.owner_user_id == two_admins["boss_id"])
    noob_person = next(p for p in persons if p.owner_user_id == two_admins["noob_id"])

    # boss 가 본인 매물에 미리 OK 받았다 체크 → sender_consent = AGREED
    _login(client, "boss@x.com")
    r = client.post(
        "/requests",
        data={
            "my_person_id": str(boss_person.id),
            "their_person_id": str(noob_person.id),
            "message": "잘 어울릴 거 같아요",
            "sender_consent": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    req = session.exec(select(IntroductionRequest)).first()
    assert req.status == IntroRequestStatus.PENDING
    assert req.sender_own_consent == SenderConsentStatus.AGREED

    # noob 가 수락 → 본인 매물 동의 표시. 양쪽 다 AGREED 지만 Encounter 안 생김.
    _login(client, "noob@x.com")
    r = client.post(f"/requests/{req.id}/accept", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "/requests" in r.headers["location"]  # encounters 가 아님

    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.PENDING  # 아직 PENDING
    assert req.sender_own_consent == SenderConsentStatus.AGREED
    assert req.receiver_own_consent == SenderConsentStatus.AGREED
    assert req.resolved_encounter_id is None
    assert session.exec(select(Encounter)).all() == []  # Encounter 0

    # 연락처 전달 완료 버튼 클릭 → Encounter 생성
    r = client.post(f"/requests/{req.id}/confirm-contact-exchanged", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/encounters/")

    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.ACCEPTED
    assert req.resolved_encounter_id is not None
    enc = session.get(Encounter, req.resolved_encounter_id)
    assert enc is not None
    assert enc.outcome == EncounterOutcome.PENDING
    assert {enc.person_a_id, enc.person_b_id} == {boss_person.id, noob_person.id}

    # 보낸이 (boss) 도 같은 버튼 클릭 가능 (양쪽 누구나)
    # — 다만 이번 케이스는 이미 ACCEPTED 이라 더 진행하면 400 거부
    _login(client, "boss@x.com")
    r2 = client.post(f"/requests/{req.id}/confirm-contact-exchanged", follow_redirects=False)
    assert r2.status_code == 400


def test_confirm_contact_exchanged_blocks_until_both_agreed(client, session, two_admins):
    """한 쪽만 AGREED 일 때 confirm-contact-exchanged 시도 → 400 거부."""
    from app.models import IntroductionRequest, Person
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
            "message": "", "sender_consent": "1",
        },
        follow_redirects=False,
    )
    req = session.exec(select(IntroductionRequest)).first()
    # sender 만 AGREED, receiver 는 NOT_ASKED 인 상태 → confirm 시도하면 거부
    r = client.post(f"/requests/{req.id}/confirm-contact-exchanged", follow_redirects=False)
    assert r.status_code == 400


def test_request_two_step_consent_then_match(client, session, two_admins):
    """양방 동의 모델 — A 가 미동의로 보냈다가, B 동의 → A 동의 순으로
    각각 표시. 마지막 동의 시점에 자동 Encounter."""
    from app.models import (
        Encounter, IntroductionRequest, IntroRequestStatus,
        Person, SenderConsentStatus,
    )
    from sqlmodel import select

    persons = session.exec(select(Person).order_by(Person.id)).all()
    boss_person = next(p for p in persons if p.owner_user_id == two_admins["boss_id"])
    noob_person = next(p for p in persons if p.owner_user_id == two_admins["noob_id"])

    # boss 가 NOT_ASKED 로 보냄
    _login(client, "boss@x.com")
    client.post(
        "/requests",
        data={"my_person_id": str(boss_person.id),
              "their_person_id": str(noob_person.id), "message": ""},
        follow_redirects=False,
    )
    req = session.exec(select(IntroductionRequest)).first()
    assert req.sender_own_consent == SenderConsentStatus.NOT_ASKED

    # noob accept → receiver consent AGREED, status 는 PENDING 유지 (A 아직 NOT_ASKED)
    _login(client, "noob@x.com")
    r = client.post(f"/requests/{req.id}/accept", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "/requests" in r.headers["location"]  # encounters 가 아님
    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.PENDING
    assert req.receiver_own_consent == SenderConsentStatus.AGREED
    assert req.resolved_encounter_id is None  # 아직 매칭 X

    # boss 가 본인 매물 동의 표시 → 양쪽 AGREED. 단 Encounter 안 생김 (연락처 전달 대기).
    _login(client, "boss@x.com")
    r = client.post(f"/requests/{req.id}/consent-agree", data={}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.PENDING  # 아직 PENDING (연락처 전달 대기)
    assert req.sender_own_consent == SenderConsentStatus.AGREED
    assert req.receiver_own_consent == SenderConsentStatus.AGREED
    assert req.resolved_encounter_id is None
    assert session.exec(select(Encounter)).all() == []  # 아직 Encounter 없음

    # 연락처 전달 완료 버튼 → 만남 생성
    r = client.post(f"/requests/{req.id}/confirm-contact-exchanged", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    req = session.get(IntroductionRequest, req.id)
    assert req.status == IntroRequestStatus.ACCEPTED
    assert req.resolved_encounter_id is not None
    assert session.get(Encounter, req.resolved_encounter_id) is not None


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
