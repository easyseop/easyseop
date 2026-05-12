from sqlalchemy import create_engine as sa_create_engine, text
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, SQLModel, create_engine, select

from .config import DATABASE_URL
from .models import Gender, Person


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


def init_db() -> None:
    _ensure_database_exists(DATABASE_URL)
    SQLModel.metadata.create_all(engine)


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
