"""통계 집계 + 페이지 접근 테스트."""
from datetime import date


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _login(client, email):
    client.cookies.clear()
    client.post("/auth/login", data={
        "email": email, "password": "pw12345678",
    }, follow_redirects=False)


def _make_persons_and_encounters(session):
    from app.models import Person, Gender, Encounter, EncounterOutcome
    a = Person(public_id="M-001", gender=Gender.M, birth_year=95, height_cm=178)
    b = Person(public_id="F-001", gender=Gender.F, birth_year=97, height_cm=165)
    c = Person(public_id="F-002", gender=Gender.F, birth_year=96, height_cm=160)
    session.add_all([a, b, c]); session.commit()
    for p in (a, b, c):
        session.refresh(p)
    # 3 만남: 1 매칭, 1 종료(ENDED_A), 1 진행중(PENDING)
    session.add_all([
        Encounter(person_a_id=a.id, person_b_id=b.id, person_a_snapshot="M-001",
                  person_b_snapshot="F-001", met_on=date(2026, 5, 1),
                  outcome=EncounterOutcome.MATCHED),
        Encounter(person_a_id=a.id, person_b_id=c.id, person_a_snapshot="M-001",
                  person_b_snapshot="F-002", met_on=date(2026, 5, 10),
                  outcome=EncounterOutcome.ENDED_A),
        Encounter(person_a_id=a.id, person_b_id=c.id, person_a_snapshot="M-001",
                  person_b_snapshot="F-002", met_on=date(2026, 6, 1),
                  outcome=EncounterOutcome.PENDING),
    ])
    session.commit()


def test_compute_stats_rates(client, session):
    from app.services.stats import compute_stats
    _register(client, "boss@x.com")  # admin 1명 있어야 matchmaker_stats 동작
    _make_persons_and_encounters(session)

    data = compute_stats(session)
    assert data["total_encounters"] == 3
    assert data["matched"] == 1
    assert data["decided"] == 2          # MATCHED + ENDED_A
    assert data["active"] == 1           # PENDING
    assert data["couple_rate"] == 50.0   # 1/2
    assert data["overall_match_rate"] == round(100 / 3, 1)  # 1/3


def test_accept_rate(client, session):
    from app.models import (
        IntroductionRequest, IntroRequestStatus, Person, Gender,
    )
    from app.services.stats import compute_stats

    _register(client, "boss@x.com")
    p = Person(public_id="M-001", gender=Gender.M, birth_year=95, height_cm=178)
    session.add(p); session.commit()
    # 3 요청: 2 수락, 1 거절 → 수락률 66.7
    for st in (IntroRequestStatus.ACCEPTED, IntroRequestStatus.ACCEPTED,
               IntroRequestStatus.DECLINED):
        session.add(IntroductionRequest(
            from_user_id=1, to_user_id=1, my_person_id=p.id, their_person_id=p.id,
            status=st,
        ))
    session.commit()
    data = compute_stats(session)
    assert data["req_total"] == 3
    assert data["accept_rate"] == 66.7


def test_stats_page_accessible_by_admin(client, session):
    _register(client, "boss@x.com")
    _make_persons_and_encounters(session)
    _login(client, "boss@x.com")
    r = client.get("/stats")
    assert r.status_code == 200
    assert "통계" in r.text
    assert "커플 성사율" in r.text


def test_matchmaker_table_owner_only(client, session):
    """책임자에겐 마담뚜별 성과 표, 일반 마담뚜에겐 숨김."""
    from app.auth import find_user_by_email

    _register(client, "boss@x.com")   # 첫 가입 = 책임자
    _register(client, "noob@x.com")
    _login(client, "boss@x.com")
    noob = find_user_by_email(session, "noob@x.com")
    client.post(f"/users/{noob.id}/toggle-admin", follow_redirects=False)

    # 책임자 → 표 보임
    _login(client, "boss@x.com")
    r = client.get("/stats")
    assert "마담뚜별 성과" in r.text

    # 일반 마담뚜 → 표 숨김
    _login(client, "noob@x.com")
    r = client.get("/stats")
    assert r.status_code == 200
    assert "마담뚜별 성과" not in r.text
