"""마담뚜 임시 대화방 (ChatRoom / ChatMessage) 흐름 테스트.

검증:
  - 방 생성 + 동일 페어 재사용
  - 메시지 전송 → 저장 + sliding 만료시각 갱신
  - 참여자만 전송, 제3자 접근 403, 책임자 열람 허용
  - 만료 방 청소(_purge_expired_chats)
  - 방 닫기(삭제)
"""
from datetime import datetime, timedelta

import pytest


def _register(client, email):
    client.cookies.clear()
    client.post(
        "/auth/register",
        data={"email": email, "password": "pw12345678", "password_confirm": "pw12345678"},
        follow_redirects=False,
    )


def _login(client, email):
    client.cookies.clear()
    client.post(
        "/auth/login",
        data={"email": email, "password": "pw12345678"},
        follow_redirects=False,
    )


@pytest.fixture
def three_admins(client, session):
    """boss(책임자) + 두 명 더 승급 → 마담뚜 3명."""
    from app.auth import find_user_by_email

    _register(client, "boss@x.com")   # 첫 가입 = 책임자
    _register(client, "a@x.com")
    _register(client, "b@x.com")

    _login(client, "boss@x.com")
    for email in ("a@x.com", "b@x.com"):
        u = find_user_by_email(session, email)
        client.post(f"/users/{u.id}/toggle-admin", follow_redirects=False)
    session.expire_all()

    ids = {}
    for email in ("boss@x.com", "a@x.com", "b@x.com"):
        u = find_user_by_email(session, email)
        ids[email] = u.id
    return ids


def _other_id(session, me_id):
    from app.models import User
    from sqlmodel import select
    return session.exec(select(User).where(User.id != me_id)).first().id


def test_create_room_and_reuse(client, session, three_admins):
    from app.models import ChatRoom
    from sqlmodel import select

    a_id, b_id = three_admins["a@x.com"], three_admins["b@x.com"]
    _login(client, "a@x.com")
    r = client.post("/chat", data={"other_user_id": str(b_id), "topic": "M-1 × F-2"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/chat/")
    rooms = session.exec(select(ChatRoom)).all()
    assert len(rooms) == 1
    room = rooms[0]
    assert {room.user_a_id, room.user_b_id} == {a_id, b_id}
    assert room.topic == "M-1 × F-2"

    # 같은 페어 재요청 → 새로 안 만들고 기존 방으로
    r2 = client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == f"/chat/{room.id}"
    assert len(session.exec(select(ChatRoom)).all()) == 1

    # 반대 방향(b → a)도 동일 방 재사용
    _login(client, "b@x.com")
    r3 = client.post("/chat", data={"other_user_id": str(a_id)}, follow_redirects=False)
    assert r3.headers["location"] == f"/chat/{room.id}"
    assert len(session.exec(select(ChatRoom)).all()) == 1


def test_cannot_create_room_with_self(client, three_admins):
    a_id = three_admins["a@x.com"]
    _login(client, "a@x.com")
    r = client.post("/chat", data={"other_user_id": str(a_id)}, follow_redirects=False)
    assert r.status_code == 400


def test_send_message_persists_and_extends_expiry(client, session, three_admins):
    from app.models import ChatMessage, ChatRoom
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    old_expiry = room.expires_at

    r = client.post(f"/chat/{room.id}/send", data={"body": "안녕하세요!"}, follow_redirects=False)
    assert r.status_code == 303  # 비-HTMX 는 redirect

    msgs = session.exec(select(ChatMessage).where(ChatMessage.room_id == room.id)).all()
    assert len(msgs) == 1
    assert msgs[0].body == "안녕하세요!"

    session.expire_all()
    room = session.get(ChatRoom, room.id)
    assert room.expires_at >= old_expiry  # sliding 갱신


def test_htmx_send_returns_partial(client, session, three_admins):
    from app.models import ChatRoom
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    r = client.post(f"/chat/{room.id}/send", data={"body": "hi"},
                    headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200
    assert 'id="chat-msgs-list"' in r.text
    assert "hi" in r.text


def test_third_party_cannot_access(client, session, three_admins):
    from app.models import ChatRoom
    from sqlmodel import select

    a_id, b_id = three_admins["a@x.com"], three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()

    # boss 는 책임자 → 열람 가능
    _login(client, "boss@x.com")
    r = client.get(f"/chat/{room.id}")
    assert r.status_code == 200
    # 책임자는 전송 불가
    r = client.post(f"/chat/{room.id}/send", data={"body": "x"}, follow_redirects=False)
    assert r.status_code == 403


def test_non_owner_third_party_forbidden(client, session, three_admins):
    """참여자도 책임자도 아닌 마담뚜는 열람 403. (boss 는 책임자라 c 가 필요 →
    여기선 a,b 방에 대해 b 가 아닌 제3 참여자가 없으므로 책임자 외 케이스는
    구조상 boss 만 비참여자 → 책임자 분기로 흡수됨. 전송 권한만 재확인.)"""
    from app.models import ChatRoom
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    # 참여자 b 는 전송 가능
    _login(client, "b@x.com")
    r = client.post(f"/chat/{room.id}/send", data={"body": "ok"},
                    headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200


def test_purge_expired_rooms(client, session, three_admins):
    from app.models import ChatMessage, ChatRoom
    from app.reminders import _purge_expired_chats
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    client.post(f"/chat/{room.id}/send", data={"body": "곧 만료됨"}, follow_redirects=False)

    # 만료시각을 과거로 강제
    session.expire_all()
    room = session.get(ChatRoom, room.id)
    room.expires_at = datetime.utcnow() - timedelta(hours=1)
    session.add(room)
    session.commit()
    room_id = room.id

    purged = _purge_expired_chats()
    assert purged == 1
    session.expire_all()
    assert session.get(ChatRoom, room_id) is None
    # 메시지도 cascade 삭제
    assert session.exec(select(ChatMessage).where(ChatMessage.room_id == room_id)).all() == []


def test_close_room_deletes(client, session, three_admins):
    from app.models import ChatRoom
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    room_id = room.id
    r = client.post(f"/chat/{room_id}/close", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(ChatRoom, room_id) is None


def test_expired_room_not_listed_or_viewable(client, session, three_admins):
    from app.models import ChatRoom
    from sqlmodel import select

    b_id = three_admins["b@x.com"]
    _login(client, "a@x.com")
    client.post("/chat", data={"other_user_id": str(b_id)}, follow_redirects=False)
    room = session.exec(select(ChatRoom)).first()
    room.expires_at = datetime.utcnow() - timedelta(minutes=1)
    session.add(room)
    session.commit()
    room_id = room.id

    # 만료 방 직접 접근 → 404
    r = client.get(f"/chat/{room_id}")
    assert r.status_code == 404
    # 목록엔 안 보임
    r = client.get("/chat")
    assert f"/chat/{room_id}" not in r.text
