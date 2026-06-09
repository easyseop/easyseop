"""서버 시작 알림 — 텔레그램 전송 + cooldown 로직."""
from datetime import datetime, timedelta


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _set_telegram(session, email, chat_id):
    from app.auth import find_user_by_email
    u = find_user_by_email(session, email)
    u.telegram_chat_id = chat_id
    session.add(u); session.commit()


def test_startup_notice_sends_when_telegram_on(client, session, monkeypatch):
    """텔레그램 활성 + chat_id 등록된 admin 에게 발송."""
    from app import notifications, startup_notice

    # COOLDOWN_FILE 격리: 매 테스트마다 깨끗하게
    if startup_notice.COOLDOWN_FILE.exists():
        startup_notice.COOLDOWN_FILE.unlink()

    _register(client, "boss@x.com")  # 첫 가입 = admin
    _set_telegram(session, "boss@x.com", "12345")

    sent_to = []
    monkeypatch.setattr(notifications, "send_telegram",
                        lambda chat_id, text: (sent_to.append(chat_id), (True, ""))[1])
    monkeypatch.setattr(notifications, "telegram_enabled", lambda: True)
    monkeypatch.setattr(startup_notice, "send_telegram",
                        lambda chat_id, text: (sent_to.append(chat_id), (True, ""))[1])
    monkeypatch.setattr(startup_notice, "telegram_enabled", lambda: True)

    n = startup_notice.send_startup_notification()
    assert n == 1
    assert sent_to == ["12345"]
    assert startup_notice.COOLDOWN_FILE.exists()


def test_startup_notice_cooldown_skips(client, session, monkeypatch):
    """직전 30분 이내 발송됐으면 스킵."""
    from app import notifications, startup_notice

    _register(client, "boss@x.com")
    _set_telegram(session, "boss@x.com", "12345")

    # 5분 전 발송 기록 마련
    recent = datetime.utcnow() - timedelta(minutes=5)
    startup_notice.COOLDOWN_FILE.write_text(recent.isoformat())

    calls = []
    monkeypatch.setattr(startup_notice, "send_telegram",
                        lambda *a, **kw: (calls.append(1), (True, ""))[1])
    monkeypatch.setattr(startup_notice, "telegram_enabled", lambda: True)

    n = startup_notice.send_startup_notification()
    assert n == 0
    assert calls == []


def test_startup_notice_after_cooldown_sends(client, session, monkeypatch):
    """30분 이상 지났으면 발송."""
    from app import notifications, startup_notice

    _register(client, "boss@x.com")
    _set_telegram(session, "boss@x.com", "12345")

    old = datetime.utcnow() - timedelta(minutes=45)
    startup_notice.COOLDOWN_FILE.write_text(old.isoformat())

    monkeypatch.setattr(startup_notice, "send_telegram",
                        lambda *a, **kw: (True, ""))
    monkeypatch.setattr(startup_notice, "telegram_enabled", lambda: True)

    n = startup_notice.send_startup_notification()
    assert n == 1


def test_startup_notice_telegram_off_skips(client, session, monkeypatch):
    from app import startup_notice
    if startup_notice.COOLDOWN_FILE.exists():
        startup_notice.COOLDOWN_FILE.unlink()

    _register(client, "boss@x.com")
    _set_telegram(session, "boss@x.com", "12345")
    monkeypatch.setattr(startup_notice, "telegram_enabled", lambda: False)

    n = startup_notice.send_startup_notification()
    assert n == 0
    assert not startup_notice.COOLDOWN_FILE.exists()
