"""매물별 조회수 — 다른 마담뚜 열람 시 +1, owner 본인은 카운트 X."""


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _login(client, email):
    client.cookies.clear()
    client.post("/auth/login", data={"email": email, "password": "pw12345678"},
                follow_redirects=False)


def test_other_admin_view_increments(client, session):
    from app.auth import find_user_by_email
    from app.models import Person

    _register(client, "boss@x.com")
    _register(client, "other@x.com")
    _login(client, "boss@x.com")
    other = find_user_by_email(session, "other@x.com")
    client.post(f"/users/{other.id}/toggle-admin", follow_redirects=False)
    boss = find_user_by_email(session, "boss@x.com")

    # boss 가 본인 매물 등록
    _login(client, "boss@x.com")
    client.post("/persons", data={
        "gender": "F", "birth_year": "97", "location": "서울",
        "workplace": "회사", "height_cm": "165", "owner_user_id": str(boss.id),
    }, follow_redirects=False)
    from sqlmodel import select
    p = session.exec(select(Person)).first()
    assert p.view_count == 0

    # owner 본인 열람 → 카운트 안 됨
    _login(client, "boss@x.com")
    client.get(f"/persons/{p.id}")
    session.expire_all()
    assert session.get(Person, p.id).view_count == 0

    # 다른 마담뚜 열람 → +1
    _login(client, "other@x.com")
    client.get(f"/persons/{p.id}")
    client.get(f"/persons/{p.id}")
    session.expire_all()
    assert session.get(Person, p.id).view_count == 2


def test_view_count_shown_on_detail(client, session):
    from app.auth import find_user_by_email
    from app.models import Person
    from sqlmodel import select

    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    client.post("/persons", data={
        "gender": "M", "birth_year": "95", "location": "서울",
        "workplace": "회사", "height_cm": "178", "owner_user_id": str(boss.id),
    }, follow_redirects=False)
    p = session.exec(select(Person)).first()
    r = client.get(f"/persons/{p.id}")
    assert r.status_code == 200
    assert "👁" in r.text
