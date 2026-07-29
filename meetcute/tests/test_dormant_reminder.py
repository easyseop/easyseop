"""2주 방치 매물 '운명을 찾고 있어요' 리마인더."""
from datetime import datetime, timedelta, date


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _mk(session, public_id, gender="F", by=95, created_days_ago=30):
    from app.models import Person, Gender
    old = datetime.utcnow() - timedelta(days=created_days_ago)
    p = Person(public_id=public_id, gender=Gender(gender), birth_year=by, height_cm=165,
               location="서울", created_at=old, updated_at=old)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _set_telegram(monkeypatch):
    from app import reminders
    sent = []
    monkeypatch.setattr(reminders, "telegram_enabled", lambda: True)
    monkeypatch.setattr(reminders, "send_telegram",
                        lambda chat_id, text: (sent.append((chat_id, text)), (True, ""))[1])
    return sent


def _baseline_days_ago(days: int):
    """방치 baseline 을 과거로 세팅 (= 기능이 그만큼 전부터 켜져 있던 것처럼)."""
    from app import reminders
    reminders.DORMANT_BASELINE_FILE.write_text(
        (datetime.utcnow() - timedelta(days=days)).isoformat()
    )


def _clear_baseline():
    from app import reminders
    if reminders.DORMANT_BASELINE_FILE.exists():
        reminders.DORMANT_BASELINE_FILE.unlink()


def test_dormant_reminder_sends(client, session, monkeypatch):
    from app.auth import find_user_by_email
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    session.add(boss); session.commit()

    _mk(session, "F-001", created_days_ago=30)  # 30일 전 등록, 활동 없음
    _baseline_days_ago(30)  # 기능이 30일 전부터 켜진 상태로 가정
    sent = _set_telegram(monkeypatch)

    n = _send_dormant_person_reminders()
    assert n == 1
    assert sent[0][0] == "111"
    assert "운명을 찾고" in sent[0][1]
    assert "F-001" in sent[0][1]


def test_baseline_delays_existing_persons(client, session, monkeypatch):
    """'지금부터 시작': baseline 이 방금이면 기존 오래된 매물도 즉시 알림 안 감.
    baseline + 2주 지나야 첫 알림."""
    from app.auth import find_user_by_email
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    session.add(boss); session.commit()

    _mk(session, "F-010", created_days_ago=60)  # 60일 전 등록 (한참 방치)
    _clear_baseline()  # baseline 없음 → 이번 실행이 '지금 켠 시점'
    sent = _set_telegram(monkeypatch)

    # 방금 켰으니(baseline=now) 아직 발송 X — 지금부터 2주 카운트
    assert _send_dormant_person_reminders() == 0
    assert sent == []

    # baseline 을 15일 전으로 옮기면(2주 경과) → 이제 발송
    _baseline_days_ago(15)
    assert _send_dormant_person_reminders() == 1


def test_recent_person_not_reminded(client, session, monkeypatch):
    from app.auth import find_user_by_email
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    session.add(boss); session.commit()

    _mk(session, "F-002", created_days_ago=3)  # 3일 전 → 아직 방치 아님
    _baseline_days_ago(30)
    sent = _set_telegram(monkeypatch)
    n = _send_dormant_person_reminders()
    assert n == 0
    assert sent == []


def test_cooldown_prevents_duplicate(client, session, monkeypatch):
    from app.auth import find_user_by_email
    from app.models import Person
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    session.add(boss); session.commit()

    p = _mk(session, "F-003", created_days_ago=30)
    _baseline_days_ago(30)
    sent = _set_telegram(monkeypatch)

    assert _send_dormant_person_reminders() == 1   # 첫 발송
    # 쿨다운 기록됨 → 곧바로 다시 돌려도 발송 X
    assert _send_dormant_person_reminders() == 0
    session.expire_all()
    assert session.get(Person, p.id).last_dormant_reminded_at is not None


def test_matched_person_excluded(client, session, monkeypatch):
    """매칭된 매물은 '운명 찾는 중' 아님 → 제외."""
    from app.auth import find_user_by_email
    from app.models import Encounter, EncounterOutcome
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    session.add(boss); session.commit()

    p = _mk(session, "F-004", created_days_ago=30)
    other = _mk(session, "M-004", gender="M", created_days_ago=30)
    # 매칭 성사 (met_on 도 오래 전이지만 status=MATCHED 라 제외돼야)
    session.add(Encounter(
        person_a_id=p.id, person_b_id=other.id,
        person_a_snapshot="F-004", person_b_snapshot="M-004",
        met_on=date.today() - timedelta(days=30), outcome=EncounterOutcome.MATCHED,
    ))
    session.commit()
    _baseline_days_ago(30)

    sent = _set_telegram(monkeypatch)
    n = _send_dormant_person_reminders()
    # F-004, M-004 둘 다 MATCHED → 제외. (다른 AVAILABLE 매물 없음)
    assert "F-004" not in "".join(t for _, t in sent)
    assert "M-004" not in "".join(t for _, t in sent)


def test_restricted_only_to_allowed(client, session, monkeypatch):
    """RESTRICTED 매물은 볼 수 있는 마담뚜의 알림에만 포함."""
    from app.auth import find_user_by_email
    from app.models import Person, PersonVisibility
    from app.reminders import _send_dormant_person_reminders

    _register(client, "boss@x.com")     # 책임자
    _register(client, "other@x.com")
    # 1) 승급 먼저 (client.post — session 쓰기 안 겹치게)
    client.cookies.clear()
    client.post("/auth/login", data={"email": "boss@x.com", "password": "pw12345678"},
                follow_redirects=False)
    other = find_user_by_email(session, "other@x.com")
    client.post(f"/users/{other.id}/toggle-admin", follow_redirects=False)
    session.expire_all()
    # 2) chat_id 세팅 후 한 번에 커밋
    boss = find_user_by_email(session, "boss@x.com")
    boss.telegram_chat_id = "111"
    other = find_user_by_email(session, "other@x.com")
    other.telegram_chat_id = "222"
    session.add(boss); session.add(other); session.commit()

    p = _mk(session, "F-005", created_days_ago=30)
    p.visibility = PersonVisibility.RESTRICTED   # 허용목록 비어있음 → boss(책임자)만 봄
    session.add(p); session.commit()
    _baseline_days_ago(30)

    sent = _set_telegram(monkeypatch)
    _send_dormant_person_reminders()
    # boss(책임자, 111)에겐 감, other(222)에겐 F-005 안 보임
    got = {chat: text for chat, text in sent}
    assert "111" in got and "F-005" in got["111"]
    assert "222" not in got  # other 에겐 보일 매물 0 → 발송 X
