"""RESTRICTED 매물이 만남 화면을 통해 누설되지 않는지 검증.

설계: 비공개 매물은 권한 없는 마담뚜에게 '🔒 비공개 매물' 로 마스킹.
만남 row 자체는 보이되, 매물의 public_id / location / workplace 등은 가려진다.
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
def three_admins_one_restricted(client, session):
    """boss(책임자), a, b 세 마담뚜. b 가 RESTRICTED 매물 F-001 등록.
    추가로 PUBLIC 매물 M-001 (a 소유), 그리고 둘이 만난 만남 기록 생성."""
    from app.auth import find_user_by_email
    from app.models import Person, PersonVisibility, Encounter, EncounterOutcome
    from datetime import date

    _register(client, "boss@x.com")
    _register(client, "a@x.com")
    _register(client, "b@x.com")
    _login(client, "boss@x.com")
    for email in ("a@x.com", "b@x.com"):
        u = find_user_by_email(session, email)
        client.post(f"/users/{u.id}/toggle-admin", follow_redirects=False)
    session.expire_all()

    a_user = find_user_by_email(session, "a@x.com")
    b_user = find_user_by_email(session, "b@x.com")

    # a 가 PUBLIC 매물 등록
    _login(client, "a@x.com")
    client.post("/persons", data={
        "gender": "M", "birth_year": "95", "location": "서울",
        "workplace": "회사", "height_cm": "178",
        "owner_user_id": str(a_user.id),
    }, follow_redirects=False)
    # b 가 PUBLIC 으로 등록 후 RESTRICTED 로 변경 (등록 폼엔 visibility 가 없고
    # 수정 폼에만 있어서 DB 에 직접 토글)
    _login(client, "b@x.com")
    client.post("/persons", data={
        "gender": "F", "birth_year": "97", "location": "강남",
        "workplace": "비밀회사", "height_cm": "165",
        "owner_user_id": str(b_user.id),
    }, follow_redirects=False)
    session.expire_all()

    from sqlmodel import select
    persons = session.exec(select(Person).order_by(Person.id)).all()
    pub_person = next(p for p in persons if p.gender.value == "M")
    restricted_person = next(p for p in persons if p.gender.value == "F")
    restricted_person.visibility = PersonVisibility.RESTRICTED
    session.add(restricted_person)
    session.commit()

    # 만남 기록 생성 (관리자 권한으로 직접)
    enc = Encounter(
        person_a_id=pub_person.id, person_b_id=restricted_person.id,
        person_a_snapshot=pub_person.public_id,
        person_b_snapshot=restricted_person.public_id,
        met_on=date.today(), outcome=EncounterOutcome.PENDING,
    )
    session.add(enc); session.commit()

    return {
        "boss_id": find_user_by_email(session, "boss@x.com").id,
        "a_id": a_user.id, "b_id": b_user.id,
        "pub_person": pub_person, "restricted_person": restricted_person,
        "enc_id": enc.id,
    }


def test_encounter_list_masks_restricted(client, three_admins_one_restricted):
    """a 는 b 의 RESTRICTED 매물을 못 보므로 /encounters 에서 마스킹돼야 함."""
    fix = three_admins_one_restricted
    _login(client, "a@x.com")
    r = client.get("/encounters")
    assert r.status_code == 200
    # 비공개 매물 public_id / location / workplace 노출 X
    assert fix["restricted_person"].public_id not in r.text
    assert "강남" not in r.text
    assert "비밀회사" not in r.text
    assert "🔒 비공개 매물" in r.text
    # 내 (PUBLIC) 매물은 정상 노출
    assert fix["pub_person"].public_id in r.text


def test_encounter_detail_masks_restricted(client, three_admins_one_restricted):
    fix = three_admins_one_restricted
    _login(client, "a@x.com")
    r = client.get(f"/encounters/{fix['enc_id']}")
    assert r.status_code == 200
    assert fix["restricted_person"].public_id not in r.text
    assert "강남" not in r.text
    assert "🔒 비공개 매물" in r.text


def test_owner_sees_everything_in_encounter(client, three_admins_one_restricted):
    """책임자(boss) 는 RESTRICTED 매물도 만남에서 그대로 봄."""
    fix = three_admins_one_restricted
    _login(client, "boss@x.com")
    r = client.get(f"/encounters/{fix['enc_id']}")
    assert r.status_code == 200
    assert fix["restricted_person"].public_id in r.text
    assert "강남" in r.text


def test_person_owner_sees_own_restricted_in_encounter(client, three_admins_one_restricted):
    """b 는 본인이 RESTRICTED 매물 owner 라 정상 봄."""
    fix = three_admins_one_restricted
    _login(client, "b@x.com")
    r = client.get(f"/encounters/{fix['enc_id']}")
    assert r.status_code == 200
    assert fix["restricted_person"].public_id in r.text


def test_new_encounter_form_hides_restricted_from_unauthorized(client, three_admins_one_restricted):
    """a 의 새 만남 폼 매물 드롭다운엔 RESTRICTED 매물 안 보임."""
    fix = three_admins_one_restricted
    _login(client, "a@x.com")
    r = client.get("/encounters/new")
    assert r.status_code == 200
    assert fix["restricted_person"].public_id not in r.text
    assert fix["pub_person"].public_id in r.text


def test_person_detail_history_masks_other_restricted(client, three_admins_one_restricted):
    """a 가 자기 PUBLIC 매물 상세를 볼 때 만남 이력에 등장하는 RESTRICTED 상대를 마스킹."""
    fix = three_admins_one_restricted
    _login(client, "a@x.com")
    r = client.get(f"/persons/{fix['pub_person'].id}")
    assert r.status_code == 200
    # 본인 매물 정보는 정상
    assert fix["pub_person"].public_id in r.text
    # 만남 상대 RESTRICTED 매물은 마스킹
    assert fix["restricted_person"].public_id not in r.text
    assert "🔒 비공개 매물" in r.text
