from datetime import datetime, date
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field, Relationship


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


class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(index=True, unique=True)  # M-001 / F-001 / X-001
    gender: Gender
    age: int
    location: str
    workplace: str
    height_cm: int
    ideal_type: str = ""
    notes: str = ""
    alias: str = ""  # 주선자용 메모 별칭 (선택)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    photos: list["Photo"] = Relationship(
        back_populates="person",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    filename: str  # uploads/ 하위 경로
    order: int = 0
    person: Optional[Person] = Relationship(back_populates="photos")


class Encounter(SQLModel, table=True):
    """소개팅 기록. Person 삭제 시 FK는 NULL, 스냅샷은 보존."""
    id: Optional[int] = Field(default=None, primary_key=True)
    person_a_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    person_b_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    person_a_snapshot: str = ""  # 예: "M-042"
    person_b_snapshot: str = ""
    met_on: date
    outcome: EncounterOutcome = EncounterOutcome.PENDING
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
