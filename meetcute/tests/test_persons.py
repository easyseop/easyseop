"""매물 CRUD + 삭제 시 Encounter 스냅샷 보존."""
import pytest


@pytest.fixture
def logged_in_owner(client):
    """첫 가입자 = 책임자, 자동 로그인 상태."""
    client.post(
        "/auth/register",
        data={
            "email": "boss@x.com",
            "password": "pw12345678",
            "password_confirm": "pw12345678",
        },
        follow_redirects=False,
    )
    return client


def test_create_person(logged_in_owner, session):
    from app.models import Person, Gender
    from sqlmodel import select

    r = logged_in_owner.post(
        "/persons",
        data={
            "gender": "M",
            "birth_year": "95",
            "location": "서울 강남",
            "workplace": "네이버",
            "height_cm": "178",
            "ideal_type": "상냥한 사람",
            "notes": "주선자 메모",
            "alias": "홍길동",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/persons/")

    ps = session.exec(select(Person)).all()
    assert len(ps) == 1
    p = ps[0]
    assert p.public_id == "M-001"
    assert p.gender == Gender.M
    assert p.birth_year == 95
    assert p.year_label == "95년생"
    assert p.location == "서울 강남"
    assert p.workplace == "네이버"
    assert p.alias == "홍길동"


def test_public_id_sequence_per_gender(logged_in_owner):
    def create(gender):
        return logged_in_owner.post(
            "/persons",
            data={
                "gender": gender,
                "birth_year": "00",
                "location": "서울",
                "workplace": "기타",
                "height_cm": "170",
            },
            follow_redirects=False,
        )

    for _ in range(3):
        create("M")
    for _ in range(2):
        create("F")
    create("M")

    from app.models import Person
    from sqlmodel import Session, select
    from app.database import engine
    with Session(engine) as s:
        pids = sorted(s.exec(select(Person.public_id)).all())
    assert pids == ["F-001", "F-002", "M-001", "M-002", "M-003", "M-004"]


def test_delete_person_preserves_encounter_snapshot(logged_in_owner, session):
    """Person 삭제 시 Encounter 의 FK 는 NULL, snapshot 에 '(deleted)' 박힘."""
    from datetime import date
    from app.models import Encounter, EncounterOutcome, Person
    from sqlmodel import select

    # 2명 등록
    for gender in ("M", "F"):
        logged_in_owner.post(
            "/persons",
            data={
                "gender": gender, "birth_year": "95", "location": "서울",
                "workplace": "기타", "height_cm": "170",
            },
            follow_redirects=False,
        )

    persons = session.exec(select(Person).order_by(Person.id)).all()
    assert len(persons) == 2
    a, b = persons

    # 만남 기록 만들기
    r = logged_in_owner.post(
        "/encounters",
        data={
            "person_a_id": str(a.id),
            "person_b_id": str(b.id),
            "met_on": date.today().isoformat(),
            "outcome": EncounterOutcome.MATCHED.value,
            "notes": "잘 됨",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    a_id = a.id
    a_pub = a.public_id

    # a 삭제
    r = logged_in_owner.post(f"/persons/{a.id}/delete", follow_redirects=False)
    assert r.status_code == 303

    session.expire_all()
    encs = session.exec(select(Encounter)).all()
    assert len(encs) == 1
    enc = encs[0]
    assert enc.person_a_id is None  # FK 끊김
    assert enc.person_a_snapshot == f"{a_pub} (deleted)"
    assert enc.person_b_id == b.id  # b 는 그대로
    # outcome 데이터 보존
    assert enc.outcome == EncounterOutcome.MATCHED
    # a 의 Person 행은 사라짐
    assert session.get(Person, a_id) is None
