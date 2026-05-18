"""활동 로그 기록 및 /activity 책임자 전용 접근."""


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


def test_activity_log_records_key_actions(client, session):
    """가입 + 로그인 + 매물 등록 → 각각 ActivityLog 행이 남음."""
    from app.models import ActivityLog
    from sqlmodel import select

    _register(client, "boss@x.com")
    # boss 는 첫 가입자라 자동 로그인 상태 — register 액션은 남음
    client.post(
        "/persons",
        data={
            "gender": "M", "birth_year": "95", "location": "서울",
            "workplace": "회사", "height_cm": "178",
        },
        follow_redirects=False,
    )

    actions = sorted(a.action for a in session.exec(select(ActivityLog)).all())
    assert "user.register" in actions
    assert "person.create" in actions


def test_activity_page_owner_only(client, session):
    """일반 마담뚜는 /activity 진입 불가, 책임자만 통과."""
    _register(client, "boss@x.com")
    _register(client, "noob@x.com")
    _login(client, "boss@x.com")
    # noob 을 admin 으로 승급 (하지만 owner 는 아님)
    from app.models import User
    from sqlmodel import select
    noob = session.exec(select(User).where(User.email == "noob@x.com")).first() \
           or next(u for u in session.exec(select(User)).all()
                   if u.email == "noob@x.com")
    client.post(f"/users/{noob.id}/toggle-admin", follow_redirects=False)

    # 책임자 boss → 200
    r = client.get("/activity", follow_redirects=False)
    assert r.status_code == 200

    # 일반 마담뚜 noob → 303 redirect (require_owner 거부)
    _login(client, "noob@x.com")
    r = client.get("/activity", follow_redirects=False)
    assert r.status_code == 303
