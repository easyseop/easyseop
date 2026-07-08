"""① 등록 시점 visibility 설정  ② 새 매물 알림에 사진 첨부."""
import io

import pytest


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


def _png_bytes():
    """1x1 PNG (Pillow 로 생성)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 100, 100)).save(buf, format="PNG")
    return buf.getvalue()


def test_create_form_shows_visibility(client):
    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    r = client.get("/persons/new")
    assert r.status_code == 200
    assert "공개 범위" in r.text
    assert 'value="RESTRICTED"' in r.text


def test_create_with_restricted_visibility(client, session):
    from app.models import Person, PersonAllowedAdmin, PersonVisibility
    from app.auth import find_user_by_email
    from sqlmodel import select

    _register(client, "boss@x.com")
    _register(client, "b@x.com")
    _login(client, "boss@x.com")
    b = find_user_by_email(session, "b@x.com")
    client.post(f"/users/{b.id}/toggle-admin", follow_redirects=False)

    _login(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    r = client.post("/persons", data={
        "gender": "F", "birth_year": "97", "location": "서울",
        "workplace": "회사", "height_cm": "165",
        "owner_user_id": str(boss.id),
        "visibility": "RESTRICTED",
        "allowed_admins": [str(b.id)],
    }, follow_redirects=False)
    assert r.status_code == 303

    p = session.exec(select(Person)).first()
    assert p.visibility == PersonVisibility.RESTRICTED
    allowed = session.exec(
        select(PersonAllowedAdmin).where(PersonAllowedAdmin.person_id == p.id)
    ).all()
    assert {a.user_id for a in allowed} == {b.id}


def test_create_default_public(client, session):
    from app.models import Person, PersonVisibility
    from sqlmodel import select

    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    client.post("/persons", data={
        "gender": "M", "birth_year": "95", "location": "서울",
        "workplace": "회사", "height_cm": "178",
    }, follow_redirects=False)
    p = session.exec(select(Person)).first()
    assert p.visibility == PersonVisibility.PUBLIC


def test_new_person_notification_sends_photos(client, session, monkeypatch):
    """사진 있는 매물 등록 → 수신자에게 send_telegram_photos 호출."""
    from app import person_events
    from app.auth import find_user_by_email

    # boss(등록자, 알림 제외) + receiver(사진 받을 사람)
    _register(client, "boss@x.com")
    _register(client, "recv@x.com")
    _login(client, "boss@x.com")
    recv = find_user_by_email(session, "recv@x.com")
    client.post(f"/users/{recv.id}/toggle-admin", follow_redirects=False)
    recv.telegram_chat_id = "999"
    session.add(recv); session.commit()

    photo_calls = []
    text_calls = []
    monkeypatch.setattr(person_events, "telegram_enabled", lambda: True)
    monkeypatch.setattr(person_events, "send_telegram_photos",
                        lambda chat_id, paths, caption="": (photo_calls.append((chat_id, len(paths))), (True, ""))[1])
    monkeypatch.setattr(person_events, "send_telegram",
                        lambda chat_id, text: (text_calls.append(chat_id), (True, ""))[1])

    # boss 가 사진 2장 붙여 등록
    _login(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    files = [
        ("photos", ("a.png", _png_bytes(), "image/png")),
        ("photos", ("b.png", _png_bytes(), "image/png")),
    ]
    r = client.post("/persons", data={
        "gender": "F", "birth_year": "97", "location": "서울",
        "workplace": "회사", "height_cm": "165",
        "owner_user_id": str(boss.id),
    }, files=files, follow_redirects=False)
    assert r.status_code == 303

    # background task 실행됨 → 사진 전송이 receiver 에게 감 (텍스트 폴백 아님)
    assert photo_calls == [("999", 2)]
    assert text_calls == []


def test_notification_falls_back_to_text_when_no_photo(client, session, monkeypatch):
    """사진 없으면 기존처럼 텍스트 알림."""
    from app import person_events
    from app.auth import find_user_by_email

    _register(client, "boss@x.com")
    _register(client, "recv@x.com")
    _login(client, "boss@x.com")
    recv = find_user_by_email(session, "recv@x.com")
    client.post(f"/users/{recv.id}/toggle-admin", follow_redirects=False)
    recv.telegram_chat_id = "999"
    session.add(recv); session.commit()

    photo_calls = []
    text_calls = []
    monkeypatch.setattr(person_events, "telegram_enabled", lambda: True)
    monkeypatch.setattr(person_events, "send_telegram_photos",
                        lambda *a, **k: (photo_calls.append(1), (True, ""))[1])
    monkeypatch.setattr(person_events, "send_telegram",
                        lambda chat_id, text: (text_calls.append(chat_id), (True, ""))[1])

    _login(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    client.post("/persons", data={
        "gender": "M", "birth_year": "95", "location": "서울",
        "workplace": "회사", "height_cm": "178",
        "owner_user_id": str(boss.id),
    }, follow_redirects=False)  # 사진 없음

    assert photo_calls == []
    assert text_calls == ["999"]
