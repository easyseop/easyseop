"""민감 필드가 DB에 실제로 암호화돼 저장되는지 + 기존 평문 일괄 변환 검증.

ORM 으로 읽으면 평문이지만, raw SQL 로 읽으면 'enc1:' 토큰이어야 한다.
"""
from sqlalchemy import text


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _raw(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).fetchall()


def test_person_sensitive_fields_encrypted_at_rest(client, session, app_env):
    """alias/notes/ideal_type/workplace 가 DB 에 enc1: 로 저장."""
    from app.models import Gender, Person

    p = Person(
        public_id="F-900", gender=Gender.F, birth_year=95, height_cm=165,
        location="서울 마포구", workplace="비밀회사", ideal_type="다정한 사람",
        notes="내부 메모", alias="지연",
    )
    session.add(p); session.commit(); session.refresh(p)

    # ORM 으로는 평문 그대로 (사용에 차이 없음)
    assert p.alias == "지연"
    assert p.notes == "내부 메모"
    assert p.workplace == "비밀회사"
    assert p.ideal_type == "다정한 사람"

    # 디스크(DB)에는 암호문
    rows = _raw(
        app_env["engine"],
        "SELECT alias, notes, ideal_type, workplace, location FROM person WHERE id = :i",
        i=p.id,
    )
    for raw_val in rows[0]:
        assert raw_val.startswith("enc1:"), f"평문 노출: {raw_val!r}"
    # 원문이 DB 문자열에 섞여있지 않은지
    joined = "".join(rows[0])
    for secret in ("지연", "내부 메모", "비밀회사", "다정한 사람", "마포구"):
        assert secret not in joined


def test_intro_request_and_encounter_notes_encrypted(client, session, app_env):
    from app.models import (
        Encounter, EncounterOutcome, Gender, IntroductionRequest, Person,
    )
    from datetime import date

    a = Person(public_id="M-900", gender=Gender.M, birth_year=93, height_cm=178)
    b = Person(public_id="F-901", gender=Gender.F, birth_year=97, height_cm=160)
    session.add_all([a, b]); session.commit(); session.refresh(a); session.refresh(b)

    enc = Encounter(
        person_a_id=a.id, person_b_id=b.id,
        person_a_snapshot="M-900", person_b_snapshot="F-901",
        met_on=date.today(), outcome=EncounterOutcome.PENDING,
        notes="만남 비밀 메모",
    )
    req = IntroductionRequest(
        from_user_id=1, to_user_id=1, my_person_id=a.id, their_person_id=b.id,
        message="소개 요청 비밀 메시지",
    )
    session.add_all([enc, req]); session.commit()
    session.refresh(enc); session.refresh(req)

    assert enc.notes == "만남 비밀 메모"          # ORM 읽기 정상
    assert req.message == "소개 요청 비밀 메시지"

    raw_enc = _raw(app_env["engine"], "SELECT notes FROM encounter WHERE id=:i", i=enc.id)[0][0]
    raw_req = _raw(app_env["engine"],
                   "SELECT message FROM introductionrequest WHERE id=:i", i=req.id)[0][0]
    assert raw_enc.startswith("enc1:") and "비밀 메모" not in raw_enc
    assert raw_req.startswith("enc1:") and "비밀 메시지" not in raw_req


def test_legacy_plaintext_gets_encrypted_on_boot(client, session, app_env):
    """예전에 평문으로 저장된 행을 init_db 가 일괄 재암호화."""
    from app.database import _encrypt_legacy_plaintext_fields
    from app.models import Gender, Person

    p = Person(public_id="F-902", gender=Gender.F, birth_year=96, height_cm=162)
    session.add(p); session.commit(); session.refresh(p)
    pid = p.id

    # 과거 상태 재현: raw SQL 로 평문 직접 주입
    engine = app_env["engine"]
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE person SET alias=:a, notes=:n WHERE id=:i"),
            {"a": "옛날이름", "n": "옛날메모", "i": pid},
        )
        conn.commit()
    raw_before = _raw(engine, "SELECT alias FROM person WHERE id=:i", i=pid)[0][0]
    assert raw_before == "옛날이름"   # 평문 상태 확인

    # 부팅 시 도는 마이그레이션 실행
    _encrypt_legacy_plaintext_fields()

    raw_after = _raw(engine, "SELECT alias, notes FROM person WHERE id=:i", i=pid)[0]
    assert raw_after[0].startswith("enc1:")
    assert raw_after[1].startswith("enc1:")
    assert "옛날이름" not in raw_after[0]

    # 앱에서는 여전히 원문 그대로 읽힘 (사용 차이 없음)
    session.expire_all()
    fresh = session.get(Person, pid)
    assert fresh.alias == "옛날이름"
    assert fresh.notes == "옛날메모"


def test_migration_is_idempotent(client, session, app_env):
    """두 번 돌려도 이중 암호화 안 됨."""
    from app.database import _encrypt_legacy_plaintext_fields
    from app.models import Gender, Person

    p = Person(public_id="F-903", gender=Gender.F, birth_year=94, height_cm=168,
               alias="이름")
    session.add(p); session.commit(); session.refresh(p)

    _encrypt_legacy_plaintext_fields()
    first = _raw(app_env["engine"], "SELECT alias FROM person WHERE id=:i", i=p.id)[0][0]
    _encrypt_legacy_plaintext_fields()
    second = _raw(app_env["engine"], "SELECT alias FROM person WHERE id=:i", i=p.id)[0][0]

    assert first == second          # 재암호화 반복 안 함
    session.expire_all()
    assert session.get(Person, p.id).alias == "이름"


def test_search_still_works_with_encrypted_alias(client, session):
    """alias 가 암호화돼도 검색은 그대로 동작 (파이썬 복호화 비교)."""
    from app.models import Gender, Person

    _register(client, "boss@x.com")
    client.cookies.clear()
    client.post("/auth/login", data={"email": "boss@x.com", "password": "pw12345678"},
                follow_redirects=False)
    session.add(Person(public_id="M-904", gender=Gender.M, birth_year=95,
                       height_cm=180, alias="동아리후배"))
    session.commit()

    r = client.get("/persons?view=list&q=동아리")
    assert r.status_code == 200
    assert "M-904" in r.text
