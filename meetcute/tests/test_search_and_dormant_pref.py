"""검색 유연화(public_id 부분/숫자) + 끌올 알림 on/off."""


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _login(client, email):
    client.cookies.clear()
    client.post("/auth/login", data={"email": email, "password": "pw12345678"},
                follow_redirects=False)


def _mk(session, public_id, gender="M", by=95):
    from app.models import Person, Gender
    p = Person(public_id=public_id, gender=Gender(gender), birth_year=by,
               height_cm=175, location="서울")
    session.add(p); session.commit()
    return p


def test_search_flexible_public_id(client, session):
    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    _mk(session, "M-051")
    _mk(session, "F-003")

    for q in ("M-051", "m-051", "051", "51"):
        r = client.get(f"/persons?view=list&q={q}")
        assert r.status_code == 200
        assert "M-051" in r.text, f"'{q}' 로 M-051 안 나옴"
        assert "F-003" not in r.text, f"'{q}' 로 F-003 잘못 나옴"


def test_search_digits_dont_overmatch(client, session):
    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    _mk(session, "M-051")
    _mk(session, "M-012")
    # '5' → 051 엔 있고 012 엔 없음
    r = client.get("/persons?view=list&q=5")
    assert "M-051" in r.text
    assert "M-012" not in r.text


def test_dormant_pref_toggle(client, session):
    from app.auth import find_user_by_email
    from app.models import User

    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    u = find_user_by_email(session, "boss@x.com")
    assert u.dormant_reminder_enabled is True  # 기본 켜짐

    # 끄기 (체크 안 함 → enabled 파라미터 없음)
    client.post("/settings/dormant-reminder", data={}, follow_redirects=False)
    session.expire_all()
    assert session.get(User, u.id).dormant_reminder_enabled is False

    # 켜기
    client.post("/settings/dormant-reminder", data={"enabled": "1"}, follow_redirects=False)
    session.expire_all()
    assert session.get(User, u.id).dormant_reminder_enabled is True


def test_dormant_reminder_skips_opted_out(client, session, monkeypatch):
    """끌올 알림 끈 마담뚜에겐 방치 알림 안 감."""
    from app import reminders
    from app.auth import find_user_by_email
    from app.models import Person, User
    from datetime import datetime, timedelta

    _register(client, "boss@x.com")
    u = find_user_by_email(session, "boss@x.com")
    u.telegram_chat_id = "111"
    u.dormant_reminder_enabled = False   # 끔
    session.add(u); session.commit()

    old = datetime.utcnow() - timedelta(days=30)
    p = Person(public_id="F-001", gender=__import__("app.models", fromlist=["Gender"]).Gender.F,
               birth_year=95, height_cm=165, location="서울", created_at=old, updated_at=old)
    session.add(p); session.commit()

    sent = []
    monkeypatch.setattr(reminders, "telegram_enabled", lambda: True)
    monkeypatch.setattr(reminders, "send_telegram",
                        lambda cid, txt: (sent.append(cid), (True, ""))[1])
    n = reminders._send_dormant_person_reminders()
    assert n == 0
    assert sent == []
