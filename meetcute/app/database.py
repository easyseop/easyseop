from sqlalchemy import create_engine as sa_create_engine, text
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, SQLModel, create_engine, select

from .config import DATABASE_URL
from .models import Gender, Person, User


def _ensure_database_exists(database_url: str) -> None:
    """MySQL/MariaDB의 경우 DB가 없으면 자동 생성. SQLite면 노옵.

    URL 형식: mysql+pymysql://user:pass@host:port/db?charset=...
    """
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        return
    db_name = url.database
    if not db_name:
        return
    # DB 없이 서버에만 접속
    server_url = url.set(database=None)
    eng = sa_create_engine(server_url)
    with eng.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    eng.dispose()


# 엔진 생성 옵션: SQLite는 thread 옵션 필요, 다른 RDBMS는 불필요.
_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
)


def _ensure_columns() -> None:
    """기존 테이블에 새 컬럼이 추가됐을 때 ALTER 로 메꾸기.
    create_all 은 누락된 테이블만 만들 뿐 누락된 컬럼은 안 만들어서, 여기서 처리.
    """
    from sqlalchemy import inspect, text

    migrations = [
        # (table, column, ddl_type)
        ("user", "nickname", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("user", "is_owner", "BOOLEAN NOT NULL DEFAULT 0"),
        ("introductionrequest", "last_reminded_at", "DATETIME"),
        ("person", "visibility", "VARCHAR(16) NOT NULL DEFAULT 'PUBLIC'"),
        ("person", "is_starred", "BOOLEAN NOT NULL DEFAULT 0"),
        ("person", "birth_year", "TEXT NOT NULL DEFAULT ''"),
        ("encounter", "last_reminded_at", "DATETIME"),
        ("introductionrequest", "sender_own_consent", "VARCHAR(16) NOT NULL DEFAULT 'NOT_ASKED'"),
        ("introductionrequest", "receiver_own_consent", "VARCHAR(16) NOT NULL DEFAULT 'NOT_ASKED'"),
        ("introductionrequest", "final_note", "TEXT NOT NULL DEFAULT ''"),
    ]
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.connect() as conn:
        for table, col, ddl in migrations:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        conn.commit()


def _backfill_nicknames_and_owner() -> None:
    """기존 유저들 닉네임이 비어있으면 랜덤 부여, 책임자가 한 명도 없으면 가장 오래된 admin 을 책임자로."""
    from .nicknames import random_nickname

    with Session(engine) as s:
        # 1) 닉네임 비어있는 유저들에 랜덤 부여
        empties = s.exec(select(User).where(User.nickname == "")).all()
        for u in empties:
            u.nickname = random_nickname()
            s.add(u)
        # 2) 책임자가 없으면 가장 오래된 관리자에게 부여
        owners = s.exec(select(User).where(User.is_owner == True)).all()  # noqa: E712
        needs_owner = not owners
        if needs_owner:
            first_admin = s.exec(
                select(User).where(User.is_admin == True).order_by(User.created_at)  # noqa: E712
            ).first()
            if first_admin:
                first_admin.is_owner = True
                s.add(first_admin)
        if empties or needs_owner:
            s.commit()


def _encrypt_legacy_user_credentials() -> None:
    """이메일/비밀번호 암호화 도입 전에 평문으로 저장된 user 행들을 일괄 재암호화.
    raw SQL 로 enc1: prefix 검사 → 없는 행만 평문→enc1: 토큰으로 직접 UPDATE.
    한 번 돌고 나면 모든 user 행이 enc1: 이라 idempotent."""
    from sqlalchemy import inspect, text

    from .crypto import encrypt_str

    insp = inspect(engine)
    if "user" not in set(insp.get_table_names()):
        return

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, password_hash FROM user")
        ).fetchall()
        updates = []
        for uid, email, pw in rows:
            new_email = email if (email or "").startswith("enc1:") else encrypt_str(email or "")
            new_pw = pw if (pw or "").startswith("enc1:") else encrypt_str(pw or "")
            if new_email != email or new_pw != pw:
                updates.append((uid, new_email, new_pw))
        for uid, new_email, new_pw in updates:
            conn.execute(
                text("UPDATE user SET email=:e, password_hash=:p WHERE id=:i"),
                {"e": new_email, "p": new_pw, "i": uid},
            )
        if updates:
            conn.commit()


def _migrate_age_to_birth_year() -> None:
    """기존 매물의 age → birth_year 추정. (현재년 - age) % 100.
    이미 birth_year 가 채워진 매물은 스킵 → idempotent.
    50세 → '76년생' (1976), 25세 → '01년생' (2001) 식 (현재 2026 기준)."""
    from datetime import date
    from .models import Person

    today_year = date.today().year
    with Session(engine) as s:
        persons = s.exec(select(Person)).all()
        changed = 0
        for p in persons:
            if p.birth_year and p.birth_year > 0:
                continue  # 이미 마이그레이션됨
            if not p.age or p.age <= 0:
                continue  # 데이터 없음
            p.birth_year = (today_year - p.age) % 100
            s.add(p)
            changed += 1
        if changed:
            s.commit()


def _backfill_accepted_consent() -> None:
    """양방 동의 모델 도입 전에 ACCEPTED 였던 요청은 양쪽 다 AGREED 로 채움.
    이미 Encounter 자동 생성됐던 거니 의미상 양쪽 동의로 봐도 무방. idempotent."""
    from .models import IntroductionRequest, IntroRequestStatus, SenderConsentStatus
    with Session(engine) as s:
        rows = s.exec(
            select(IntroductionRequest).where(
                IntroductionRequest.status == IntroRequestStatus.ACCEPTED
            )
        ).all()
        changed = 0
        for r in rows:
            updated = False
            if r.sender_own_consent != SenderConsentStatus.AGREED:
                r.sender_own_consent = SenderConsentStatus.AGREED
                updated = True
            if r.receiver_own_consent != SenderConsentStatus.AGREED:
                r.receiver_own_consent = SenderConsentStatus.AGREED
                updated = True
            if updated:
                s.add(r); changed += 1
        if changed:
            s.commit()


def init_db() -> None:
    _ensure_database_exists(DATABASE_URL)
    SQLModel.metadata.create_all(engine)
    _ensure_columns()
    _backfill_nicknames_and_owner()
    _encrypt_legacy_user_credentials()
    _migrate_age_to_birth_year()
    _backfill_accepted_consent()


def get_session():
    with Session(engine) as session:
        yield session


_PREFIX = {Gender.M: "M", Gender.F: "F", Gender.OTHER: "X"}


def next_public_id(session: Session, gender: Gender) -> str:
    """성별별 시퀀스 발급. 같은 prefix의 max 번호 + 1."""
    prefix = _PREFIX[gender]
    rows = session.exec(
        select(Person.public_id).where(Person.public_id.startswith(f"{prefix}-"))
    ).all()
    nums = []
    for pid in rows:
        try:
            nums.append(int(pid.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    nxt = (max(nums) + 1) if nums else 1
    return f"{prefix}-{nxt:03d}"
