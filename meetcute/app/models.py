from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field, Relationship, SQLModel

from .crypto import EncryptedText, IntEncryptedText, LegacyEncryptedText


# ─── 긴 텍스트는 명시적으로 TEXT 컬럼 (MySQL의 VARCHAR-without-length 회피) ────
def _text_col() -> Column:
    return Column(Text, nullable=False, default="")


def _text_col_required() -> Column:
    return Column(Text, nullable=False)


# ─── 암호화된 텍스트 컬럼: 거주지/직장/(나이) 등 민감 식별 가능 정보
def _enc_text_col() -> Column:
    return Column(EncryptedText(), nullable=False, default="")


def _enc_text_col_required() -> Column:
    return Column(EncryptedText(), nullable=False)


# ─── 평문 저장 + 옛 enc1: 데이터 자동 복호화 (점진 마이그레이션)
def _legacy_enc_text_col() -> Column:
    return Column(LegacyEncryptedText(), nullable=False, default="")


def _legacy_enc_text_col_required() -> Column:
    return Column(LegacyEncryptedText(), nullable=False)


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
    # 이메일/비밀번호는 암호화 저장. Fernet 은 비결정적이라 DB unique/index 무의미.
    # 중복 체크는 app 단에서 scan + decrypt 로 (find_user_by_email 헬퍼).
    email: str = Field(sa_column=Column(EncryptedText(), nullable=False))
    # bcrypt 해시를 한 번 더 Fernet 으로. DB 만 털리고 .encryption_key 안 털린
    # 경우 오프라인 bcrypt 크래킹 차단 (defense-in-depth).
    password_hash: str = Field(sa_column=Column(EncryptedText(), nullable=False))
    nickname: str = Field(default="", max_length=64)  # 다른 마담뚜 한테 표시되는 이름
    is_admin: bool = False
    is_owner: bool = False   # 책임자 (배포자). 첫 가입자 자동 부여. 다른 마담뚜의 민감정보(텔레그램 chat_id 등) 열람 가능.
    telegram_chat_id: str = Field(default="", max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def display_name(self) -> str:
        """다른 마담뚜 한테 보여지는 안전한 이름. 이메일 절대 노출 안 함.
        nickname 이 이메일 형태면 (자동완성/실수로 들어간 경우) 폴백."""
        nick = self.nickname.strip() if self.nickname else ""
        if not nick:
            return f"마담뚜-{self.id}"
        # 이메일 패턴 차단: a@b.c
        if "@" in nick and "." in nick.split("@", 1)[-1]:
            return f"마담뚜-{self.id}"
        return nick


class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(index=True, unique=True, max_length=16)
    gender: Gender = Field(sa_column=_enum_col(Gender))
    # 암호화 대상: 거주지/직장/나이 (실제로 사람 식별에 도움 되는 PII)
    # age 는 레거시 — 출생연도 표시 도입 후엔 새 entry 에선 0 으로 둠.
    # 기존 매물은 마이그레이션 시 birth_year 계산 후에도 값 유지 (히스토리 호환).
    age: int = Field(default=0, sa_column=Column(IntEncryptedText(), nullable=False))
    # 출생연도 2자리. 50-99 = 19xx, 00-49 = 20xx 로 해석. 새 entry 의 메인 필드.
    birth_year: int = Field(default=0, sa_column=Column(IntEncryptedText(), nullable=False))
    location: str = Field(default="", sa_column=_enc_text_col())
    workplace: str = Field(default="", sa_column=_legacy_enc_text_col())
    height_cm: int
    # 메모/취향 — 평문 + 옛 enc1: 데이터는 자동 복호화 (점진 마이그레이션)
    ideal_type: str = Field(default="", sa_column=_legacy_enc_text_col())
    notes: str = Field(default="", sa_column=_legacy_enc_text_col())
    alias: str = Field(default="", sa_column=_legacy_enc_text_col())
    owner_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    visibility: PersonVisibility = Field(
        default=PersonVisibility.PUBLIC,
        sa_column=_enum_col(PersonVisibility),
    )
    # 즐겨찾기 마킹 — 마담뚜가 "이 매물 좀 더 신경쓰자" 표시.
    # 정렬/필터에 사용. 단일 글로벌 플래그 (마담뚜별 개인 즐겨찾기 아님).
    is_starred: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    photos: list["Photo"] = Relationship(
        back_populates="person",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def year_label(self) -> str:
        """'99년생' 같은 표시. birth_year 가 0 이면 (옛 데이터) age 폴백."""
        if self.birth_year and self.birth_year > 0:
            return f"{self.birth_year:02d}년생"
        if self.age and self.age > 0:
            return f"{self.age}세"  # 마이그레이션 전 옛 데이터 폴백
        return "—"

    @property
    def years_old_approx(self) -> int:
        """birth_year 에서 추정한 만 나이 (생일 모르니 ±1 오차). 호환성 등에 사용."""
        if not self.birth_year:
            return self.age or 0
        from datetime import date
        today = date.today()
        full_year = 1900 + self.birth_year if self.birth_year >= 50 else 2000 + self.birth_year
        return today.year - full_year


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
    notes: str = Field(default="", sa_column=_legacy_enc_text_col())
    # 후속 리마인더: PENDING 7일+ 또는 CONTINUING 30일+ 이면 양쪽 owner 에게
    # 텔레그램 알림 ("결과 어떻게 됐어요?"). 보낸 시각 기록해서 중복 방지.
    last_reminded_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonRevision(SQLModel, table=True):
    """매물 정보 수정 시 변경 직전 상태를 스냅샷으로 보관.

    snapshot_json: 수정 직전의 텍스트 필드 dict를 JSON 문자열로 저장.
    포함 필드: age, location, workplace, height_cm, ideal_type, notes, alias.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    snapshot_json: str = Field(sa_column=_legacy_enc_text_col_required())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class IntroRequestStatus(str, Enum):
    PENDING = "PENDING"       # 받은 사람이 아직 응답 안 함
    ACCEPTED = "ACCEPTED"     # 받은 사람이 OK → 만남 기록 자동 생성
    DECLINED = "DECLINED"     # 받은 사람이 NO
    WITHDRAWN = "WITHDRAWN"   # 보낸 사람이 취소


class SenderConsentStatus(str, Enum):
    """보낸이(A)가 본인 매물에게 의향 물어본 상태.
    - NOT_ASKED: 아직 안 물어봄 (B 가 "먼저 물어봐줘" 회신 가능)
    - AGREED: 매물 동의 — 정상 진행
    - DECLINED: 매물 거절 — 요청 자동 종결 (status → WITHDRAWN)
    """
    NOT_ASKED = "NOT_ASKED"
    AGREED = "AGREED"
    DECLINED = "DECLINED"


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
    message: str = Field(default="", sa_column=_legacy_enc_text_col())
    status: IntroRequestStatus = Field(
        default=IntroRequestStatus.PENDING,
        sa_column=_enum_col(IntroRequestStatus),
    )
    response_note: str = Field(default="", sa_column=_legacy_enc_text_col())
    # 보낸이가 본인 매물에 의향 물어본 상태. 기본 NOT_ASKED.
    sender_own_consent: SenderConsentStatus = Field(
        default=SenderConsentStatus.NOT_ASKED,
        sa_column=_enum_col(SenderConsentStatus),
    )
    resolved_encounter_id: Optional[int] = Field(default=None, foreign_key="encounter.id")
    last_reminded_at: Optional[datetime] = Field(default=None)  # 마지막 재알림 시각 (없으면 한 번도 안 보냄)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BlacklistedPair(SQLModel, table=True):
    """A와 B는 절대 매칭하지 말 것 마킹. 호환성 페이지에서 자동 제외, 만남/요청
    생성 시 경고. 페어는 canonical 형태로 저장 (min(id), max(id)) — 양방향
    중복 행 방지."""
    person_a_id: int = Field(foreign_key="person.id", primary_key=True)
    person_b_id: int = Field(foreign_key="person.id", primary_key=True)
    reason: str = Field(default="", sa_column=_legacy_enc_text_col())
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_by_display: str = Field(default="", max_length=128)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityLog(SQLModel, table=True):
    """마담뚜 활동 통합 로그. 책임자가 누가-언제-뭘 했는지 한눈에 보려고.

    - PersonRevision (필드 diff) / EncounterEvent (outcome 변화) 와 별개로
      엔드포인트 단위 행동을 잡아놓는 광역 audit log.
    - actor_user_id: nullable (시스템/익명 액션 대비). actor_display 는 유저가
      삭제돼도 누가 했는지 남기려고 따로 박아둠.
    - action: 'login' | 'person.create' | 'request.accept' 같은 도트표기.
    - summary: 사람이 읽을 한 줄 요약 (선택). LegacyEncryptedText — 평문 저장.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    actor_display: str = Field(default="", max_length=128)
    action: str = Field(max_length=64, index=True)
    target_type: str = Field(default="", max_length=32)
    target_id: Optional[int] = Field(default=None)
    summary: str = Field(default="", sa_column=_legacy_enc_text_col())
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class EncounterEvent(SQLModel, table=True):
    """만남 한 건의 outcome 변화 이력.

    - Encounter 생성 시 초기 outcome 로 한 건 자동 기록.
    - update에서 outcome이 바뀌면 한 건 추가 (note 는 그 시점의 부가설명).
    - notes-only 변경은 이벤트 안 만듦 (소음 방지).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    encounter_id: int = Field(foreign_key="encounter.id", index=True)
    outcome: EncounterOutcome = Field(sa_column=_enum_col(EncounterOutcome))
    note: str = Field(default="", sa_column=_legacy_enc_text_col())
    changed_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_by_email: str = Field(default="", max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
