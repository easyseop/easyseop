"""비밀번호 재설정 플로우 — forgot 페이지 → 토큰 발급 → reset 페이지 → 비번 변경."""


def _register(client, email, pw="initialpass123"):
    client.cookies.clear()
    client.post(
        "/auth/register",
        data={"email": email, "password": pw, "password_confirm": pw},
        follow_redirects=False,
    )


def test_forgot_page_renders(client):
    r = client.get("/auth/forgot")
    assert r.status_code == 200
    assert "비밀번호 재설정" in r.text


def test_login_page_has_forgot_link(client):
    r = client.get("/auth/login")
    assert "/auth/forgot" in r.text


def test_forgot_submit_returns_generic_ok_for_unknown_email(client):
    """이메일 enumeration 방지: 알 수 없는 이메일도 같은 성공 응답."""
    r = client.post(
        "/auth/forgot", data={"email": "ghost@x.com"}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/auth/forgot?ok=" in r.headers["location"]


def test_forgot_without_telegram_no_token_created(client, session):
    """텔레그램 chat_id 없는 유저는 토큰 발급 안 됨 (조용히)."""
    from app.models import PasswordResetToken
    from sqlmodel import select

    _register(client, "noteleg@x.com")
    client.cookies.clear()
    r = client.post(
        "/auth/forgot", data={"email": "noteleg@x.com"}, follow_redirects=False,
    )
    assert r.status_code == 303
    tokens = session.exec(select(PasswordResetToken)).all()
    assert tokens == []


def test_reset_with_bad_token_shows_expired(client):
    r = client.get("/auth/reset/totallyfake")
    assert r.status_code == 200
    # forgot 페이지로 안내하는 만료 메시지
    assert "만료" in r.text or "사용" in r.text


def test_full_reset_flow_with_telegram_user(client, session, monkeypatch):
    """텔레그램 chat_id 등록된 유저 → forgot → 토큰 발급 → reset → 비번 변경 + 자동 로그인."""
    from app.models import PasswordResetToken
    from app.auth import find_user_by_email, verify_password
    from sqlmodel import select

    _register(client, "tel@x.com", pw="oldpassword123")
    user = find_user_by_email(session, "tel@x.com")
    user.telegram_chat_id = "9999"
    session.add(user)
    session.commit()

    # 텔레그램 전송 stub
    sent = {}
    def fake_send(chat_id, text):
        sent["chat_id"] = chat_id
        sent["text"] = text
        return True, ""
    monkeypatch.setattr("app.notifications.send_telegram", fake_send)
    monkeypatch.setattr("app.notifications.telegram_enabled", lambda: True)

    client.cookies.clear()
    r = client.post(
        "/auth/forgot", data={"email": "tel@x.com"}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert sent.get("chat_id") == "9999"
    assert "/auth/reset/" in sent["text"]

    # 토큰 DB 에 1개
    session.expire_all()
    tokens = session.exec(select(PasswordResetToken)).all()
    assert len(tokens) == 1
    # token 자체는 메시지에서 파싱
    import re
    m = re.search(r"/auth/reset/([A-Za-z0-9_-]+)", sent["text"])
    assert m
    raw_token = m.group(1)

    # reset GET — 유효 폼 노출
    r = client.get(f"/auth/reset/{raw_token}")
    assert r.status_code == 200
    assert "새 비밀번호" in r.text

    # reset POST — 비번 변경 + 자동 로그인 (대시보드로 redirect)
    r = client.post(
        f"/auth/reset/{raw_token}",
        data={"new_password": "brandnewpassword1", "new_password_confirm": "brandnewpassword1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/")

    # 새 비번으로 로그인 가능
    session.expire_all()
    user = find_user_by_email(session, "tel@x.com")
    assert verify_password("brandnewpassword1", user.password_hash)
    assert not verify_password("oldpassword123", user.password_hash)

    # 토큰 재사용 차단
    r = client.post(
        f"/auth/reset/{raw_token}",
        data={"new_password": "another1234567", "new_password_confirm": "another1234567"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/auth/forgot" in r.headers["location"]


def test_reset_password_mismatch_rejected(client, session, monkeypatch):
    from app.auth import create_password_reset_token, find_user_by_email

    _register(client, "mm@x.com")
    user = find_user_by_email(session, "mm@x.com")
    raw = create_password_reset_token(session, user)
    r = client.post(
        f"/auth/reset/{raw}",
        data={"new_password": "abc1234567890", "new_password_confirm": "different12345"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert f"/auth/reset/{raw}" in r.headers["location"]
    assert "error=" in r.headers["location"]
