"""매물 목록 출생연도(나이) 범위 필터."""


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


def _mk(session, public_id, gender, by):
    from app.models import Person, Gender
    p = Person(public_id=public_id, gender=Gender(gender), birth_year=by, height_cm=170)
    session.add(p)
    session.commit()
    return p


def test_birth_year_range_filter(client, session):
    _register(client, "boss@x.com")
    # 95(1995), 99(1999), 00(2000), 03(2003)
    _mk(session, "F-095", "F", 95)
    _mk(session, "F-099", "F", 99)
    _mk(session, "M-000", "M", 0)
    _mk(session, "M-003", "M", 3)
    _login(client, "boss@x.com")

    # 부터=99(1999) ~ 까지=00(2000) → F-099, M-000
    r = client.get("/persons?view=list&birth_from=99&birth_to=00")
    assert r.status_code == 200
    assert "F-099" in r.text and "M-000" in r.text
    assert "F-095" not in r.text and "M-003" not in r.text


def test_birth_from_only(client, session):
    _register(client, "boss@x.com")
    _mk(session, "F-090", "F", 90)  # 1990
    _mk(session, "F-005", "F", 5)   # 2005
    _login(client, "boss@x.com")
    # 부터=00(2000) 이상 → 2005 만 (1990 제외)
    r = client.get("/persons?view=list&birth_from=00")
    assert "F-005" in r.text
    assert "F-090" not in r.text


def test_birth_to_only(client, session):
    _register(client, "boss@x.com")
    _mk(session, "F-090", "F", 90)  # 1990
    _mk(session, "F-005", "F", 5)   # 2005
    _login(client, "boss@x.com")
    # 까지=99(1999) 이하 → 1990 만
    r = client.get("/persons?view=list&birth_to=99")
    assert "F-090" in r.text
    assert "F-005" not in r.text


def test_birth_filter_ignores_bad_input(client, session):
    _register(client, "boss@x.com")
    _mk(session, "F-095", "F", 95)
    _login(client, "boss@x.com")
    # 빈/잘못된 값 → 필터 무시, 전부 보임
    r = client.get("/persons?view=list&birth_from=&birth_to=abc")
    assert r.status_code == 200
    assert "F-095" in r.text
