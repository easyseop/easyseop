"""가입 → 첫 가입자 자동 책임자 → 로그인 → 로그아웃 흐름."""


def _register(client, email, pw):
    return client.post(
        "/auth/register",
        data={"email": email, "password": pw, "password_confirm": pw},
        follow_redirects=False,
    )


def test_register_first_user_becomes_owner(client, session):
    from app.models import User
    from sqlmodel import select

    r = _register(client, "boss@x.com", "pw12345678")
    assert r.status_code == 303
    assert r.headers["location"] == "/"  # 첫 가입자는 바로 대시보드

    users = session.exec(select(User)).all()
    assert len(users) == 1
    assert users[0].is_admin is True
    assert users[0].is_owner is True


def test_second_user_pending_approval(client, session):
    """두 번째 가입자는 admin/owner 아님 → /auth/pending 으로 보내짐."""
    from app.models import User
    from sqlmodel import select

    _register(client, "boss@x.com", "pw12345678")
    # 첫 가입자는 자동 로그인 됨 → 세션 비워야 두 번째 가입 가능
    client.cookies.clear()
    r = _register(client, "noob@x.com", "pw12345678")
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/pending"

    users = session.exec(select(User).order_by(User.created_at)).all()
    assert len(users) == 2
    assert users[1].is_admin is False
    assert users[1].is_owner is False


def test_login_logout_flow(client, session):
    _register(client, "boss@x.com", "pw12345678")
    client.cookies.clear()  # register 자동로그인 해제

    # 잘못된 비번
    r = client.post(
        "/auth/login",
        data={"email": "boss@x.com", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    # 올바른 비번
    r = client.post(
        "/auth/login",
        data={"email": "boss@x.com", "password": "pw12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    # 보호 라우트 접근
    r = client.get("/persons", follow_redirects=False)
    assert r.status_code == 200

    # 로그아웃 → 보호 라우트 다시 막힘
    client.post("/auth/logout", follow_redirects=False)
    r = client.get("/persons", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/login" in r.headers["location"]
