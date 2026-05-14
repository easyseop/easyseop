"""샘플 데이터 시드 스크립트.

사용:
    python -m app.seed              # 데이터 있으면 거부
    python -m app.seed --force      # 기존 매물/만남/이벤트/revision 다 지우고 새로 채움

가짜 사진(컬러 정사각형 + public_id 텍스트)도 1-2장씩 생성합니다.
"""
import argparse
import shutil
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw
from sqlmodel import Session, select

from .config import UPLOAD_DIR
from .database import engine, init_db, next_public_id
from .models import (
    Encounter,
    EncounterEvent,
    EncounterOutcome,
    Gender,
    Person,
    PersonRevision,
    Photo,
)


# 색 팔레트 (사진 생성용)
PALETTE = [
    (236, 72, 153),   # 핑크
    (251, 146, 60),   # 오렌지
    (250, 204, 21),   # 옐로
    (132, 204, 22),   # 라임
    (34, 197, 94),    # 그린
    (45, 212, 191),   # 틸
    (96, 165, 250),   # 블루
    (167, 139, 250),  # 퍼플
]


def _placeholder_photo(person_id: int, public_id: str, idx: int) -> str:
    """간단한 컬러 정사각형 + public_id 텍스트."""
    color = PALETTE[(person_id + idx) % len(PALETTE)]
    img = Image.new("RGB", (400, 400), color=color)
    d = ImageDraw.Draw(img)
    # 폰트 없이 기본 비트맵 폰트로 ID 출력 (200x200 중앙 즈음)
    d.text((140, 170), public_id, fill="white")
    d.text((140, 200), f"#{idx + 1}", fill=(255, 255, 255, 180))

    person_dir = UPLOAD_DIR / str(person_id)
    person_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.png"
    img.save(person_dir / name)
    return f"{person_id}/{name}"


PERSONAS = [
    # gender, age, location, workplace, height_cm, ideal_type, notes, alias, photo_count
    (Gender.F, 28, "서울 마포구", "스타트업 마케팅팀 매니저", 165,
     "유머있고 자기 일 좋아하는 사람. 키 175 이상 선호.",
     "동아리 후배 추천. 매우 적극적.",
     "동아리 후배", 2),
    (Gender.F, 26, "서울 강남구", "IT 회사 UX 디자이너", 162,
     "조용한 편이고 영화/전시 같이 보러 갈 수 있는 사람.",
     "주변 친구 통해 알게 됨. 사진 별로 안 좋아함.",
     "친구의 친구", 1),
    (Gender.F, 31, "경기 판교", "로펌 변호사", 170,
     "안정적인 직장. 듬직하고 책임감 있는 사람.",
     "조건 좋음. 본인 일정 빡빡한 편.",
     "회사 선배", 2),
    (Gender.F, 29, "서울 성동구", "대학병원 내과 전공의", 168,
     "가정적이고 술 너무 자주 안 마시는 사람.",
     "근무가 불규칙해서 평일 만남 어려움.",
     "병원 후배", 1),
    (Gender.M, 30, "서울 송파구", "테크 회사 백엔드 엔지니어", 178,
     "활발하고 운동/여행 좋아하는 사람.",
     "사진 많이 가져옴. 본인이 적극적.",
     "고등 동창", 2),
    (Gender.M, 32, "서울 강남구", "대형 병원 정형외과 전문의", 180,
     "차분하고 진중한 사람. 책 좋아하면 좋겠음.",
     "조건 가장 좋음. 만남 신중함.",
     "지인 추천", 2),
    (Gender.M, 27, "경기 분당", "IT 회사 PM", 175,
     "에너지 많고 함께 새로운 거 시도하는 사람.",
     "약간 어린 편. 연상 거부감 없음.",
     "회사 후배", 1),
    (Gender.M, 33, "서울 마포구", "에이전시 디자인 디렉터", 173,
     "예술/문화 코드 맞는 사람. 외모 많이 봄.",
     "본인이 까다로운 편.",
     "친구", 2),
]


def _seed(session: Session, force: bool) -> None:
    existing = session.exec(select(Person)).first()
    if existing and not force:
        print("❌ 이미 매물 데이터가 있어요. 다시 채우려면 --force 옵션:")
        print("    python -m app.seed --force")
        sys.exit(1)

    if force:
        print("🧹 기존 데이터 정리 중...")
        for model in (EncounterEvent, Encounter, PersonRevision, Photo, Person):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()
        # 업로드 폴더도 비우기
        if UPLOAD_DIR.exists():
            for p in UPLOAD_DIR.iterdir():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)

    # ── 매물 등록 ──
    persons: list[Person] = []
    print("👥 매물 등록 중...")
    for (g, age, loc, work, h, ideal, notes, alias, photo_count) in PERSONAS:
        pid = next_public_id(session, g)
        p = Person(
            public_id=pid,
            gender=g,
            age=age,
            location=loc,
            workplace=work,
            height_cm=h,
            ideal_type=ideal,
            notes=notes,
            alias=alias,
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        for i in range(photo_count):
            rel = _placeholder_photo(p.id, p.public_id, i)
            session.add(Photo(person_id=p.id, filename=rel, order=i))
        session.commit()
        persons.append(p)
        print(f"  {p.public_id}: {age}세 {loc} ({alias})")

    # ── 정보 수정 이력 한두 개 (revision 데모용) ──
    print("📝 매물 정보 수정 이력 데모 (2건)...")
    import json
    p = persons[0]  # F-001
    snap = {f: getattr(p, f) for f in ("age", "location", "workplace", "height_cm",
                                       "ideal_type", "notes", "alias")}
    session.add(PersonRevision(
        person_id=p.id, snapshot_json=json.dumps(snap, ensure_ascii=False),
        changed_by_email="(seed)",
        changed_at=datetime.utcnow() - timedelta(days=30),
    ))
    p.workplace = "스타트업 마케팅 팀장"  # 승진
    p.age = 29
    session.add(p)

    p = persons[4]  # M-001
    snap = {f: getattr(p, f) for f in ("age", "location", "workplace", "height_cm",
                                       "ideal_type", "notes", "alias")}
    session.add(PersonRevision(
        person_id=p.id, snapshot_json=json.dumps(snap, ensure_ascii=False),
        changed_by_email="(seed)",
        changed_at=datetime.utcnow() - timedelta(days=10),
    ))
    p.location = "서울 강동구"  # 이사
    p.notes = (p.notes + "\n2026-04: 강동구로 이사함.").strip()
    session.add(p)
    session.commit()

    # ── 만남 기록 + 결과 변경 이벤트 ──
    print("💞 만남 기록 등록 중...")
    today = date.today()
    by_id = {p.public_id: p for p in persons}

    def _enc(a_pid: str, b_pid: str, days_ago: int, transitions: list[tuple]):
        """transitions: [(outcome, days_after_initial, note), ...].
        첫 항목이 초기 outcome. 나머지는 그 이후의 변경."""
        a = by_id[a_pid]
        b = by_id[b_pid]
        met = today - timedelta(days=days_ago)
        initial_outcome, _, initial_note = transitions[0]
        enc = Encounter(
            person_a_id=a.id, person_b_id=b.id,
            person_a_snapshot=a.public_id, person_b_snapshot=b.public_id,
            met_on=met,
            outcome=initial_outcome,
            notes=initial_note,
        )
        session.add(enc)
        session.commit()
        session.refresh(enc)
        base_ts = datetime.combine(met, datetime.min.time())
        session.add(EncounterEvent(
            encounter_id=enc.id,
            outcome=initial_outcome,
            note="최초 등록",
            changed_by_email="(seed)",
            created_at=base_ts,
        ))
        cur = initial_outcome
        for outcome, days_after, note in transitions[1:]:
            cur = outcome
            ts = base_ts + timedelta(days=days_after)
            session.add(EncounterEvent(
                encounter_id=enc.id,
                outcome=outcome,
                note=note,
                changed_by_email="(seed)",
                created_at=ts,
            ))
        # encounter.outcome 은 마지막 transition 결과로
        enc.outcome = cur
        enc.updated_at = datetime.utcnow()
        session.add(enc)
        session.commit()
        print(f"  {a_pid} × {b_pid} ({met}): {' → '.join(t[0].value for t in transitions)}")

    # 매칭 성공 — 풀 스토리
    _enc("F-001", "M-001", days_ago=80, transitions=[
        (EncounterOutcome.PENDING, 0, "친구 통해 첫 만남"),
        (EncounterOutcome.CONTINUING, 7, "1차 좋았다 함. 2차 잡힘."),
        (EncounterOutcome.MATCHED, 35, "사귀기로 했다고 연락 옴 🎉"),
    ])

    # 거절 — A가
    _enc("F-002", "M-003", days_ago=110, transitions=[
        (EncounterOutcome.PENDING, 0, "공통 친구 통해 만남"),
        (EncounterOutcome.ENDED_A, 5, "F가 톤 안 맞다고."),
    ])

    # 거절 — B가
    _enc("F-002", "M-002", days_ago=60, transitions=[
        (EncounterOutcome.PENDING, 0, ""),
        (EncounterOutcome.ENDED_B, 12, "M이 연락 끊김."),
    ])

    # 진행 중 — PENDING
    _enc("F-003", "M-003", days_ago=35, transitions=[
        (EncounterOutcome.PENDING, 0, "조건은 잘 맞음. 1차 잡힘."),
    ])

    # 진행 중 — CONTINUING
    _enc("F-004", "M-004", days_ago=20, transitions=[
        (EncounterOutcome.PENDING, 0, "사진 보고 호감 표시."),
        (EncounterOutcome.CONTINUING, 4, "1차 잘 됨, 2차 잡음."),
    ])

    # 양쪽 다 노
    _enc("F-001", "M-002", days_ago=120, transitions=[
        (EncounterOutcome.PENDING, 0, ""),
        (EncounterOutcome.MUTUAL_END, 9, "둘 다 톤 안 맞는다고."),
    ])

    # 빠른 매칭 (조건 잘 맞음)
    _enc("F-004", "M-002", days_ago=150, transitions=[
        (EncounterOutcome.PENDING, 0, "둘 다 의사라 통할 듯."),
        (EncounterOutcome.MATCHED, 21, "사귀게 됨 — 3주 만에."),
    ])

    # 오래된 진행 중 (잠자는 매칭 후보)
    _enc("F-003", "M-001", days_ago=95, transitions=[
        (EncounterOutcome.PENDING, 0, ""),
        (EncounterOutcome.ENDED_B, 14, "M쪽 거절."),
    ])

    print("\n✅ 시드 완료!")
    print("  매물 8명 / 만남 8건 / 이벤트 17건 / revision 2건 / 사진 12장")
    print("  → ./dev.sh 또는 'uvicorn app.main:app --reload --port 8765' → http://127.0.0.1:8765")


def main():
    parser = argparse.ArgumentParser(description="meetcute 샘플 데이터 시드")
    parser.add_argument("--force", action="store_true",
                        help="기존 데이터 모두 삭제하고 새로 채움")
    args = parser.parse_args()

    init_db()
    with Session(engine) as session:
        _seed(session, force=args.force)


if __name__ == "__main__":
    main()
