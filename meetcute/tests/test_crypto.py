"""Field-level 암호화 라운드트립 + 백워드 호환."""


def test_encrypt_decrypt_roundtrip(app_env):
    from app.crypto import encrypt_str, decrypt_str
    token = encrypt_str("서울 강남")
    assert token.startswith("enc1:")
    assert decrypt_str(token) == "서울 강남"


def test_plaintext_passthrough(app_env):
    """prefix 없는 값은 평문으로 간주 (백워드 호환)."""
    from app.crypto import decrypt_str
    assert decrypt_str("plain text") == "plain text"
    assert decrypt_str("") == ""


def test_int_encrypted_text(app_env):
    from app.crypto import IntEncryptedText
    t = IntEncryptedText()
    # write: int → enc1: 텍스트
    cipher = t.process_bind_param(30, None)
    assert cipher.startswith("enc1:")
    # read: 다시 int
    assert t.process_result_value(cipher, None) == 30
    # 옛 INTEGER 컬럼에서 그대로 읽힌 int 도 호환
    assert t.process_result_value(42, None) == 42


def test_user_email_password_encrypted_in_db(app_env, session):
    """User.email 과 password_hash 는 enc1: 으로 저장됨 (raw SQLite 확인)."""
    import sqlite3
    from app.auth import hash_password
    from app.models import User

    u = User(
        email="alice@example.com",
        password_hash=hash_password("pw12345678"),
        nickname="alice",
        is_admin=True,
        is_owner=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)

    conn = sqlite3.connect(str(app_env["db_path"]))
    row = conn.execute(
        "SELECT email, password_hash FROM user WHERE id=?", (u.id,)
    ).fetchone()
    conn.close()
    assert row[0].startswith("enc1:")
    assert row[1].startswith("enc1:")
