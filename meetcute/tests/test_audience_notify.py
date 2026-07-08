"""공개 대상 기반 알림 + 중복 방지 (PersonNotified).

시나리오:
  - RESTRICTED 로 등록 → 허용 대상만 알림
  - 공개 대상 확대(허용 추가) → 새 대상만 알림, 기존 대상 중복 X
  - RESTRICTED → PUBLIC → 아직 안 받은 나머지 전원
"""
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


@pytest.fixture
def admins(client, session):
    """boss(책임자) + a,b,c 마담뚜. 전원 telegram chat_id 등록."""
    from app.auth import find_user_by_email

    _register(client, "boss@x.com")
    for e in ("a@x.com", "b@x.com", "c@x.com"):
        _register(client, e)
    _login(client, "boss@x.com")
    # 1) 먼저 승급 (client.post — 자체 세션). 이때 test session 은 쓰기 없이 읽기만.
    for e in ("a@x.com", "b@x.com", "c@x.com"):
        u = find_user_by_email(session, e)
        client.post(f"/users/{u.id}/toggle-admin", follow_redirects=False)
    session.expire_all()
    # 2) 그 다음 chat_id 세팅 (한 번에 커밋 — client.post 와 안 겹침)
    ids = {}
    for e in ("boss@x.com", "a@x.com", "b@x.com", "c@x.com"):
        u = find_user_by_email(session, e)
        u.telegram_chat_id = e  # chat_id = 이메일 (구분용)
        session.add(u)
        ids[e] = u.id
    session.commit()
    return ids


@pytest.fixture(autouse=True)
def _telegram_capture(monkeypatch):
    """전역: 텔레그램 켠 것처럼 + 발송 대상 캡처."""
    from app import person_events
    sent = []
    monkeypatch.setattr(person_events, "telegram_enabled", lambda: True)
    monkeypatch.setattr(person_events, "send_telegram",
                        lambda chat_id, text: (sent.append(chat_id), (True, ""))[1])
    monkeypatch.setattr(person_events, "send_telegram_photos",
                        lambda chat_id, paths, caption="": (sent.append(chat_id), (True, ""))[1])
    person_events._sent_capture = sent
    return sent


def _create_restricted(client, session, owner_email, owner_id, allowed_ids):
    _login(client, owner_email)
    data = {
        "gender": "F", "birth_year": "97", "location": "서울",
        "workplace": "회사", "height_cm": "165",
        "owner_user_id": str(owner_id),
        "visibility": "RESTRICTED",
        "allowed_admins": [str(x) for x in allowed_ids],
    }
    client.post("/persons", data=data, follow_redirects=False)
    from app.models import Person
    from sqlmodel import select
    return session.exec(select(Person)).first()


def test_restricted_notifies_only_allowed(client, session, admins, _telegram_capture):
    """boss 가 a 만 허용해서 RESTRICTED 등록 → a 만 알림 (b,c 제외, boss 는 등록자)."""
    boss = admins["boss@x.com"]
    p = _create_restricted(client, session, "boss@x.com", boss, [admins["a@x.com"]])
    # boss=등록자(제외), 허용=a, 책임자=boss(등록자라 제외) → a 만
    assert _telegram_capture == ["a@x.com"]


def test_audience_expand_notifies_only_new(client, session, admins, _telegram_capture):
    """a 허용 → 나중에 b 추가 → b 만 알림 (a 중복 X)."""
    boss = admins["boss@x.com"]
    p = _create_restricted(client, session, "boss@x.com", boss, [admins["a@x.com"]])
    assert _telegram_capture == ["a@x.com"]
    _telegram_capture.clear()

    # 수정: 허용에 b 추가 (a + b)
    _login(client, "boss@x.com")
    client.post(f"/persons/{p.id}", data={
        "birth_year": "97", "location": "서울", "workplace": "회사", "height_cm": "165",
        "owner_user_id": str(boss),
        "visibility": "RESTRICTED",
        "allowed_admins": [str(admins["a@x.com"]), str(admins["b@x.com"])],
    }, follow_redirects=False)
    # b 만 새로 알림
    assert _telegram_capture == ["b@x.com"]


def test_restricted_to_public_notifies_rest(client, session, admins, _telegram_capture):
    """a 허용으로 등록 → PUBLIC 전환 → 아직 안 받은 b,c 에게만 (a 중복 X)."""
    boss = admins["boss@x.com"]
    p = _create_restricted(client, session, "boss@x.com", boss, [admins["a@x.com"]])
    _telegram_capture.clear()

    _login(client, "boss@x.com")
    client.post(f"/persons/{p.id}", data={
        "birth_year": "97", "location": "서울", "workplace": "회사", "height_cm": "165",
        "owner_user_id": str(boss),
        "visibility": "PUBLIC",
    }, follow_redirects=False)
    # 이미 받은 a 제외, boss 는 등록자(기록됨). b,c 만.
    assert set(_telegram_capture) == {"b@x.com", "c@x.com"}


def test_no_change_no_notify(client, session, admins, _telegram_capture):
    """공개 대상 변화 없는 수정(텍스트만) → 아무 알림 X."""
    boss = admins["boss@x.com"]
    p = _create_restricted(client, session, "boss@x.com", boss, [admins["a@x.com"]])
    _telegram_capture.clear()

    _login(client, "boss@x.com")
    client.post(f"/persons/{p.id}", data={
        "birth_year": "98", "location": "부산", "workplace": "회사", "height_cm": "166",
        "owner_user_id": str(boss),
        "visibility": "RESTRICTED",
        "allowed_admins": [str(admins["a@x.com"])],  # 동일
    }, follow_redirects=False)
    assert _telegram_capture == []
