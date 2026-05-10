from sqlmodel import SQLModel, Session, create_engine, select
from .config import DATABASE_URL
from .models import Person, Gender

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
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
