from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field, Relationship, SQLModel

from .crypto import EncryptedText


# ─── 긴 텍스트는 명시적으로 TEXT 컬럼 (MySQL의 VARCHAR-without-length 회피) ────
def _text_col() -> Column:
    return Column(Text, nullable=False, default="")


def _text_col_required() -> Column:
    return Column(Text, nullable=False)


# ─── 암호화된 텍스트 컬럼 (개인정보/메모 등 민감한 필드)
def _enc_text_col() -> Column:
    return Column(EncryptedText(), nullable=False, default="")


def _enc_text_col_required() -> Column:
    return Column(EncryptedText(), nullable=False)


# ─── enum 컬럼: SQLAlchemy ENUM 으로 (MySQL: ENUM 타입, SQLite: VARCHAR + CHECK)
def _enum_col(enum_cls) -> Column:
    return Column(SAEnum(enum_cls), nullable=False)


class Gender(str, Enum):
    M = "M"
    F = "F"
    OTHER = "OTHER"

    @property
    def label(self) -> str:
        return {"M": "남자", "F": "여자", "OTHER": "기타"}.get(self.value, self.value)


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


class PersonVisibility(str, Enum):
    PUBLIC = "PUBLIC"          # 모든 admin 에게 보임 (기본)
    RESTRICTED = "RESTRICTED"  # owner + 책임자 + 명시된 admin 만


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=255)
    password_hash: str = Field(max_length=255)
    nickname: str = Field(default="", max_length=64)  # 다른 admin 한테 표시되는 이름
    is_admin: bool = False
    is_owner: bool = False   # 책임자 (배포자). 첫 가입자 자동 부여. 다른 admin의 민감정보(텔레그램 chat_id 등) 열람 가능.
    telegram_chat_id: str = Field(default="", max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def display_name(self) -> str:
        """다른 admin 한테 보여지는 안전한 이름. 이메일 절대 노출 안 함.
        nickname 이 이메일 형태면 (자동완성/실수로 들어간 경우) 폴백."""
        nick = self.nickname.strip() if self.nickname else ""
        if not nick:
            return f"admin-{self.id}"
        # 이메일 패턴 차단: a@b.c
        if "@" in nick and "." in nick.split("@", 1)[-1]:
            return f"admin-{self.id}"
        return nick


class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(index=True, unique=True, max_length=16)
    gender: Gender = Field(sa_column=_enum_col(Gender))
    age: int
    location: str = Field(max_length=255)
    workplace: str = Field(max_length=255)
    height_cm: int
    ideal_type: str = Field(default="", sa_column=_enc_text_col())
    notes: str = Field(default="", sa_column=_enc_text_col())
    alias: str = Field(default="", sa_column=_enc_text_col())
    owner_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    visibility: PersonVisibility = Field(
        default=PersonVisibility.PUBLIC,
        sa_column=_enum_col(PersonVisibility),
    )
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


class PersonAllowedAdmin(SQLModel, table=True):
    """visibility=RESTRICTED 인 매물에 대한 허용 admin 목록 (many-to-many)."""
    person_id: int = Field(foreign_key="person.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)


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
    notes: str = Field(default="", sa_column=_enc_text_col())
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonRevision(SQLModel, table=True):
    """매물 정보 수정 시 변경 직전 상태를 스냅샷으로 보관.

    snapshot_json: 수정 직전의 텍스트 필드 dict를 JSON 문자열로 저장.
    포함 필드: age, location, workplace, height_cm, ideal_type, notes, alias.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    snapshot_json: str = Field(sa_column=_enc_text_col_required())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class IntroRequestStatus(str, Enum):
    PENDING = "PENDING"       # 받은 사람이 아직 응답 안 함
    ACCEPTED = "ACCEPTED"     # 받은 사람이 OK → 만남 기록 자동 생성
    DECLINED = "DECLINED"     # 받은 사람이 NO
    WITHDRAWN = "WITHDRAWN"   # 보낸 사람이 취소


class IntroductionRequest(SQLModel, table=True):
    """A의 매물 (my_person) 을 B의 매물 (their_person) 에 소개해달라는 요청.

    - from_user_id: 요청 보낸 admin (A)
    - to_user_id:   요청 받은 admin (B, their_person 의 owner)
    - my_person_id: A 가 소개하려는 매물
    - their_person_id: B 가 운영하는 매물 (소개 대상)
    - 수락되면 Encounter 자동 생성 → resolved_encounter_id 에 연결
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    from_user_id: int = Field(foreign_key="user.id", index=True)
    to_user_id: int = Field(foreign_key="user.id", index=True)
    my_person_id: int = Field(foreign_key="person.id", index=True)
    their_person_id: int = Field(foreign_key="person.id", index=True)
    message: str = Field(default="", sa_column=_enc_text_col())
    status: IntroRequestStatus = Field(
        default=IntroRequestStatus.PENDING,
        sa_column=_enum_col(IntroRequestStatus),
    )
    response_note: str = Field(default="", sa_column=_enc_text_col())
    resolved_encounter_id: Optional[int] = Field(default=None, foreign_key="encounter.id")
    last_reminded_at: Optional[datetime] = Field(default=None)  # 마지막 재알림 시각 (없으면 한 번도 안 보냄)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EncounterEvent(SQLModel, table=True):
    """만남 한 건의 outcome 변화 이력.

    - Encounter 생성 시 초기 outcome 로 한 건 자동 기록.
    - update에서 outcome이 바뀌면 한 건 추가 (note 는 그 시점의 부가설명).
    - notes-only 변경은 이벤트 안 만듦 (소음 방지).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    encounter_id: int = Field(foreign_key="encounter.id", index=True)
    outcome: EncounterOutcome = Field(sa_column=_enum_col(EncounterOutcome))
    note: str = Field(default="", sa_column=_enc_text_col())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
