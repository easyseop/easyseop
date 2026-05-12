from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field, Relationship, SQLModel


# ─── 긴 텍스트는 명시적으로 TEXT 컬럼 (MySQL의 VARCHAR-without-length 회피) ────
def _text_col() -> Column:
    return Column(Text, nullable=False, default="")


def _text_col_required() -> Column:
    return Column(Text, nullable=False)


# ─── enum 컬럼: SQLAlchemy ENUM 으로 (MySQL: ENUM 타입, SQLite: VARCHAR + CHECK)
def _enum_col(enum_cls) -> Column:
    return Column(SAEnum(enum_cls), nullable=False)


class Gender(str, Enum):
    M = "M"
    F = "F"
    OTHER = "OTHER"


class EncounterOutcome(str, Enum):
    PENDING = "PENDING"        # 만남 잡힘 / 결과 미정
    CONTINUING = "CONTINUING"  # 계속 만나는 중
    MATCHED = "MATCHED"        # 매칭 성공 (사귀기로)
    ENDED_A = "ENDED_A"        # A가 거절
    ENDED_B = "ENDED_B"        # B가 거절
    MUTUAL_END = "MUTUAL_END"  # 양쪽 다 노

    @property
    def is_active(self) -> bool:
        return self in (EncounterOutcome.PENDING, EncounterOutcome.CONTINUING)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=255)
    password_hash: str = Field(max_length=255)
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(index=True, unique=True, max_length=16)
    gender: Gender = Field(sa_column=_enum_col(Gender))
    age: int
    location: str = Field(max_length=255)
    workplace: str = Field(max_length=255)
    height_cm: int
    ideal_type: str = Field(default="", sa_column=_text_col())
    notes: str = Field(default="", sa_column=_text_col())
    alias: str = Field(default="", max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    photos: list["Photo"] = Relationship(
        back_populates="person",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    filename: str = Field(max_length=512)  # uploads/ 하위 경로
    order: int = 0
    person: Optional[Person] = Relationship(back_populates="photos")


class Encounter(SQLModel, table=True):
    """소개팅 기록. Person 삭제 시 FK는 NULL, 스냅샷은 보존."""
    id: Optional[int] = Field(default=None, primary_key=True)
    person_a_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    person_b_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    person_a_snapshot: str = Field(default="", max_length=64)  # 예: "M-042 (deleted)"
    person_b_snapshot: str = Field(default="", max_length=64)
    met_on: date
    outcome: EncounterOutcome = Field(
        default=EncounterOutcome.PENDING,
        sa_column=_enum_col(EncounterOutcome),
    )
    notes: str = Field(default="", sa_column=_text_col())
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonRevision(SQLModel, table=True):
    """매물 정보 수정 시 변경 직전 상태를 스냅샷으로 보관.

    snapshot_json: 수정 직전의 텍스트 필드 dict를 JSON 문자열로 저장.
    포함 필드: age, location, workplace, height_cm, ideal_type, notes, alias.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    snapshot_json: str = Field(sa_column=_text_col_required())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class EncounterEvent(SQLModel, table=True):
    """만남 한 건의 outcome 변화 이력.

    - Encounter 생성 시 초기 outcome 로 한 건 자동 기록.
    - update에서 outcome이 바뀌면 한 건 추가 (note 는 그 시점의 부가설명).
    - notes-only 변경은 이벤트 안 만듦 (소음 방지).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    encounter_id: int = Field(foreign_key="encounter.id", index=True)
    outcome: EncounterOutcome = Field(sa_column=_enum_col(EncounterOutcome))
    note: str = Field(default="", sa_column=_text_col())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
