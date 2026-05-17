import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy.orm import defer

from ..auth import get_current_user
from ..config import AUTH_ENABLED, UPLOAD_DIR
from ..database import get_session, next_public_id
from ..person_events import notify_new_person
from ..models import (
    Encounter,
    Gender,
    IntroRequestStatus,
    IntroductionRequest,
    Person,
    PersonAllowedAdmin,
    PersonVisibility,
    Photo,
    User,
)
from ..services.activity import (
    ActivityStats,
    activity_for_person,
    activity_for_persons,
)
from ..services.revisions import (
    diff_against,
    diff_between,
    record_revision,
    revisions_for_person,
)
from ..services.status import (
    encounters_for_person,
    derive_status,
    status_badge_class,
    status_label,
    statuses_for_persons,
)
from ..templating import templates

router = APIRouter(prefix="/persons", tags=["persons"])

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PHOTOS = 5
# 모바일 원본 사진(5–10MB) 그대로 두면 카드 그리드가 무거움.
# 긴 변 기준 리사이즈 + JPEG quality 압축 → 보통 1/10 이하로 줄어듦.
PHOTO_MAX_DIM = 1600
PHOTO_JPEG_QUALITY = 85
PHOTO_WEBP_QUALITY = 85


def _save_photo(person_id: int, upload: UploadFile) -> str:
    """사진 저장. EXIF orientation 자동 보정 + 긴 변 1600px 리사이즈 + 압축.
    HEIC 는 PIL 기본 미지원 → 원본 복사."""
    from PIL import Image, ImageOps

    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    person_dir = UPLOAD_DIR / str(person_id)
    person_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = person_dir / name

    if ext == ".heic":
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        return f"{person_id}/{name}"

    try:
        img = Image.open(upload.file)
        img = ImageOps.exif_transpose(img)
        # 긴 변이 PHOTO_MAX_DIM 보다 크면 비율 유지 축소
        w, h = img.size
        if max(w, h) > PHOTO_MAX_DIM:
            img.thumbnail((PHOTO_MAX_DIM, PHOTO_MAX_DIM), Image.LANCZOS)
        # JPEG 는 RGBA 지원 X — 변환
        if ext in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        save_kwargs: dict = {}
        if ext in (".jpg", ".jpeg"):
            save_kwargs = {"quality": PHOTO_JPEG_QUALITY, "optimize": True, "progressive": True}
        elif ext == ".webp":
            save_kwargs = {"quality": PHOTO_WEBP_QUALITY, "method": 6}
        elif ext == ".png":
            save_kwargs = {"optimize": True}
        img.save(dest, **save_kwargs)
    except Exception:
        # 파싱 실패 시 원본 그대로
        upload.file.seek(0)
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
    return f"{person_id}/{name}"


@router.get("", response_class=HTMLResponse)
def list_persons(
    request: Request,
    gender: Optional[str] = None,    # "" 또는 None 또는 enum value
    q: Optional[str] = None,
    status: Optional[str] = None,    # 'AVAILABLE' | 'IN_PROGRESS' | 'MATCHED' | None
    activity: Optional[str] = None,  # 'never' | 'dormant' | 'active' | None
    owner: Optional[str] = None,     # 'mine' | 'unassigned' | 'user:<id>' | 'others' | None
    sort: Optional[str] = None,      # 'recent_activity' | 'dormant' | 'created' (default)
    view: Optional[str] = None,      # 'list' | 'card' (default 'card')
    session: Session = Depends(get_session),
):
    current_user = get_current_user(request, session)

    # gender 빈 문자열은 무시 (enum 검증 우회)
    gender_enum: Optional[Gender] = None
    if gender:
        try:
            gender_enum = Gender(gender)
        except ValueError:
            gender_enum = None

    # 목록 view 에선 ideal_type / notes (둘 다 암호화·긴 텍스트) 안 보여줌 → defer 로
    # SELECT 에서 빼서 row 당 복호화 비용 절감
    stmt = select(Person).options(defer(Person.ideal_type), defer(Person.notes))
    if gender_enum:
        stmt = stmt.where(Person.gender == gender_enum)
    if q:
        like = f"%{q}%"
        # 거주지/직장은 암호화 저장 → DB LIKE 가 안 됨. 검색은 public_id / alias 만.
        # alias 는 LegacyEncryptedText: 새 데이터는 평문이라 LIKE 가능 (옛 enc1: 데이터는 안 잡힘 → 한 번 저장하면 평문화).
        stmt = stmt.where(
            (Person.public_id.like(like))
            | (Person.alias.like(like))
        )
    persons = session.exec(stmt).all()

    # 공개범위 필터: 책임자가 아니라면 RESTRICTED 매물 중 허락 안 된 것 제외
    if AUTH_ENABLED and current_user and current_user.id and not current_user.is_owner:
        allowed_set = _allowed_person_ids_for_user(session, current_user.id)
        persons = [p for p in persons if _can_see_person(p, current_user, allowed_set)]

    # owner 필터 (AUTH=on 일 때만 의미)
    if AUTH_ENABLED and current_user and current_user.id:
        if owner == "mine":
            persons = [p for p in persons if p.owner_user_id == current_user.id]
        elif owner == "others":  # 하위 호환
            persons = [p for p in persons
                       if p.owner_user_id and p.owner_user_id != current_user.id]
        elif owner == "unassigned":
            persons = [p for p in persons if p.owner_user_id is None]
        elif owner and owner.startswith("user:"):
            try:
                target_uid = int(owner.split(":", 1)[1])
                persons = [p for p in persons if p.owner_user_id == target_uid]
            except ValueError:
                pass

    # owner 정보 매핑 (목록 카드에 표시)
    owner_map: dict[int, User] = {}
    if AUTH_ENABLED:
        owner_ids = {p.owner_user_id for p in persons if p.owner_user_id}
        if owner_ids:
            rows = session.exec(select(User).where(User.id.in_(list(owner_ids)))).all()
            owner_map = {u.id: u for u in rows}

    statuses = statuses_for_persons(session, persons)
    stats = activity_for_persons(session, persons)

    # 상태 필터 (소개가능/진행중/매칭됨)
    if status in ("AVAILABLE", "IN_PROGRESS", "MATCHED"):
        persons = [p for p in persons if statuses.get(p.id) and statuses.get(p.id).value == status]

    if activity == "never":
        persons = [p for p in persons if stats[p.id].never_met]
    elif activity == "active":
        persons = [p for p in persons if stats[p.id].active > 0]
    elif activity == "dormant":
        # 30일 이상 미활동 + 매칭/진행 없음
        persons = [
            p for p in persons
            if (stats[p.id].never_met or (stats[p.id].days_dormant or 0) >= 30)
            and stats[p.id].active == 0 and stats[p.id].matched == 0
        ]

    if sort == "recent_activity":
        persons.sort(
            key=lambda p: stats[p.id].last_activity or __import__("datetime").date.min,
            reverse=True,
        )
    elif sort == "recent_update":
        # 최근 정보 수정순 (updated_at 최신)
        persons.sort(key=lambda p: p.updated_at or p.created_at, reverse=True)
    elif sort == "dormant":
        # 미활동 오래된 것이 위로 (never_met = 가장 위)
        def k(p):
            s = stats[p.id]
            if s.never_met:
                return (0, p.created_at)
            return (1, s.last_activity)
        persons.sort(key=k)
    else:
        persons.sort(key=lambda p: p.created_at, reverse=True)

    # 필터 드롭다운에 보여줄 admin 목록 (AUTH=on 만)
    admin_list: list[User] = []
    if AUTH_ENABLED:
        admin_list = session.exec(
            select(User).where(User.is_admin == True).order_by(User.created_at)  # email 은 암호화돼 정렬키 못 씀  # noqa: E712
        ).all()

    return templates.TemplateResponse(
        request,
        "persons/list.html",
        {
            "persons": persons,
            "statuses": statuses,
            "stats": stats,
            "owner_map": owner_map,
            "current_user": current_user,
            "admin_list": admin_list,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "gender": gender or "",
            "q": q or "",
            "status": status or "",
            "activity": activity or "",
            "owner": owner or "",
            "sort": sort or "",
            "view": view if view in ("card", "list") else "card",
        },
    )


def _admins(session: Session) -> list[User]:
    return session.exec(select(User).where(User.is_admin == True).order_by(User.email)).all()  # noqa: E712


def _allowed_person_ids_for_user(session: Session, user_id: int) -> set[int]:
    rows = session.exec(
        select(PersonAllowedAdmin.person_id).where(PersonAllowedAdmin.user_id == user_id)
    ).all()
    return set(rows)


def _can_see_person(
    person: Person,
    user: Optional[User],
    allowed_set: Optional[set[int]] = None,
    session: Optional[Session] = None,
) -> bool:
    """RESTRICTED 매물은 owner + 책임자 + 허용된 admin 만 볼 수 있음.
    PUBLIC 은 모든 admin. AUTH=off 면 항상 허용."""
    if not AUTH_ENABLED:
        return True
    if not user or not user.id:
        return False
    if user.is_owner:
        return True
    if person.owner_user_id == user.id:
        return True
    if person.visibility == PersonVisibility.PUBLIC:
        return True
    # RESTRICTED
    if allowed_set is not None:
        return person.id in allowed_set
    if session is None:
        return False
    paa = session.exec(
        select(PersonAllowedAdmin).where(
            PersonAllowedAdmin.person_id == person.id,
            PersonAllowedAdmin.user_id == user.id,
        )
    ).first()
    return paa is not None


def _require_view(person: Person, request: Request, session: Session) -> None:
    user = get_current_user(request, session)
    if not _can_see_person(person, user, session=session):
        raise HTTPException(403, "이 매물을 볼 권한이 없습니다 (비공개 설정)")


def _can_edit_person(person: Person, user: Optional[User]) -> bool:
    """등록한 owner 만 수정 가능. 책임자(is_owner) 는 모든 매물 수정 가능.
    미지정 매물은 누구나 (담아가게). AUTH=off (로컬) 면 항상 허용."""
    if not AUTH_ENABLED:
        return True
    if not user or not user.id:
        return False
    if user.is_owner:
        return True  # 책임자는 무제한
    if person.owner_user_id is None:
        return True  # 미지정 매물은 어느 admin 이나 (담아가게)
    return person.owner_user_id == user.id


def _require_edit(person: Person, request: Request, session: Session) -> None:
    user = get_current_user(request, session)
    if not _can_edit_person(person, user):
        raise HTTPException(403, "이 매물은 등록한 마담뚜만 수정할 수 있습니다")


@router.get("/new", response_class=HTMLResponse)
def new_person_form(request: Request, session: Session = Depends(get_session)):
    current_user = get_current_user(request, session)
    return templates.TemplateResponse(
        request,
        "persons/form.html",
        {
            "person": None,
            "Gender": Gender,
            "admins": _admins(session) if AUTH_ENABLED else [],
            "current_user": current_user,
        },
    )


@router.post("")
async def create_person(
    request: Request,
    background_tasks: BackgroundTasks,
    gender: Gender = Form(...),
    age: int = Form(...),
    location: str = Form(...),
    workplace: str = Form(...),
    height_cm: int = Form(...),
    ideal_type: str = Form(""),
    notes: str = Form(""),
    alias: str = Form(""),
    owner_user_id: str = Form(""),  # 빈 문자열 = unassigned
    photos: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    public_id = next_public_id(session, gender)

    # owner 결정: 폼 select 에서 명시적으로 "— 미지정 —" (value="") 고르면 None 으로 저장.
    # 폼 기본값이 current_user 라 그냥 등록 누르면 current_user 가 들어옴.
    resolved_owner_id: Optional[int] = None
    if AUTH_ENABLED and owner_user_id.strip():
        try:
            resolved_owner_id = int(owner_user_id)
        except ValueError:
            resolved_owner_id = None

    person = Person(
        public_id=public_id,
        gender=gender,
        age=age,
        location=location,
        workplace=workplace,
        height_cm=height_cm,
        ideal_type=ideal_type,
        notes=notes,
        alias=alias,
        owner_user_id=resolved_owner_id,
    )
    session.add(person)
    session.commit()
    session.refresh(person)

    for i, upload in enumerate(photos[:MAX_PHOTOS]):
        if not upload.filename:
            continue
        rel = _save_photo(person.id, upload)
        session.add(Photo(person_id=person.id, filename=rel, order=i))
    session.commit()

    # 새 매물 등록 알림 — 공개 범위 안의 다른 마담뚜 들에게 (등록자 제외)
    actor = get_current_user(request, session)
    background_tasks.add_task(
        notify_new_person, person.id, actor.id if actor and actor.id else None
    )

    return RedirectResponse(f"/persons/{person.id}", status_code=303)


@router.get("/{person_id}", response_class=HTMLResponse)
def person_detail(
    person_id: int, request: Request, session: Session = Depends(get_session)
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    _require_view(person, request, session)
    photos = sorted(person.photos, key=lambda p: p.order)
    encs = encounters_for_person(session, person.id)
    status = derive_status(encs)
    activity = activity_for_person(session, person.id)
    revisions = revisions_for_person(session, person.id)

    current_user = get_current_user(request, session)
    owner = session.get(User, person.owner_user_id) if person.owner_user_id else None
    is_my_person = bool(
        AUTH_ENABLED and current_user and current_user.id
        and person.owner_user_id == current_user.id
    )
    # "내 매물 → 다른 마담뚜 소유의 매물에 소개 요청 보내기" CTA 노출 조건
    can_send_intro_request = bool(
        AUTH_ENABLED and current_user and current_user.id
        and person.owner_user_id
        and person.owner_user_id != current_user.id
    )
    can_edit = _can_edit_person(person, current_user)

    # Revision diff 계산: 최신 revision은 현재 person과 비교,
    # 이전 것들은 그 다음 revision과 비교 (revisions는 desc 정렬)
    revision_diffs: list[tuple] = []
    for i, rev in enumerate(revisions):
        if i == 0:
            diff = diff_against(rev.snapshot_json, person)
        else:
            diff = diff_between(rev.snapshot_json, revisions[i - 1].snapshot_json)
        revision_diffs.append((rev, diff))

    # 상대방 매물 정보 매핑 (삭제된 경우 None)
    other_ids = {
        (e.person_b_id if e.person_a_id == person.id else e.person_a_id)
        for e in encs
    }
    other_ids.discard(None)
    others = {}
    if other_ids:
        rows = session.exec(select(Person).where(Person.id.in_(list(other_ids)))).all()
        others = {p.id: p for p in rows}
    from .encounters import OUTCOME_LABEL, OUTCOME_BADGE  # 순환 import 회피
    return templates.TemplateResponse(
        request,
        "persons/detail.html",
        {
            "person": person,
            "photos": photos,
            "encounters": encs,
            "others": others,
            "status": status,
            "activity": activity,
            "revision_diffs": revision_diffs,
            "owner": owner,
            "current_user": current_user,
            "is_my_person": is_my_person,
            "can_send_intro_request": can_send_intro_request,
            "can_edit": can_edit,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "OUTCOME_LABEL": OUTCOME_LABEL,
            "OUTCOME_BADGE": OUTCOME_BADGE,
        },
    )


@router.get("/{person_id}/edit", response_class=HTMLResponse)
def edit_person_form(
    person_id: int, request: Request, session: Session = Depends(get_session)
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    _require_edit(person, request, session)
    current_user = get_current_user(request, session)

    # 현재 허용된 admin id 집합 (visibility 폼 채울 용)
    allowed_admin_ids: set[int] = set()
    if AUTH_ENABLED:
        rows = session.exec(
            select(PersonAllowedAdmin.user_id).where(PersonAllowedAdmin.person_id == person.id)
        ).all()
        allowed_admin_ids = set(rows)

    return templates.TemplateResponse(
        request,
        "persons/form.html",
        {
            "person": person,
            "Gender": Gender,
            "PersonVisibility": PersonVisibility,
            "admins": _admins(session) if AUTH_ENABLED else [],
            "allowed_admin_ids": allowed_admin_ids,
            "current_user": current_user,
        },
    )


@router.post("/{person_id}")
async def update_person(
    request: Request,
    person_id: int,
    age: int = Form(...),
    location: str = Form(...),
    workplace: str = Form(...),
    height_cm: int = Form(...),
    ideal_type: str = Form(""),
    notes: str = Form(""),
    alias: str = Form(""),
    owner_user_id: str = Form(""),
    visibility: str = Form("PUBLIC"),
    allowed_admins: list[int] = Form(default=[]),
    photos: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    _require_edit(person, request, session)

    # 변경이 있을 때만 revision 기록 (사진만 추가하는 경우는 스킵)
    text_changed = (
        person.age != age
        or person.location != location
        or person.workplace != workplace
        or person.height_cm != height_cm
        or person.ideal_type != ideal_type
        or person.notes != notes
        or person.alias != alias
    )
    if text_changed:
        actor = get_current_user(request, session)
        record_revision(session, person, actor)

    person.age = age
    person.location = location
    person.workplace = workplace
    person.height_cm = height_cm
    person.ideal_type = ideal_type
    person.notes = notes
    person.alias = alias
    if AUTH_ENABLED:
        if owner_user_id.strip():
            try:
                person.owner_user_id = int(owner_user_id)
            except ValueError:
                person.owner_user_id = None
        else:
            person.owner_user_id = None

        # visibility 변경은 owner (또는 책임자) 만 가능
        actor = get_current_user(request, session)
        can_change_vis = bool(actor and (actor.is_owner or person.owner_user_id == actor.id))
        if can_change_vis:
            try:
                new_vis = PersonVisibility(visibility)
            except ValueError:
                new_vis = PersonVisibility.PUBLIC
            person.visibility = new_vis
            # 기존 허용 목록 비우고 다시 채움
            old = session.exec(
                select(PersonAllowedAdmin).where(PersonAllowedAdmin.person_id == person.id)
            ).all()
            for paa in old:
                session.delete(paa)
            if new_vis == PersonVisibility.RESTRICTED:
                for uid in allowed_admins:
                    try:
                        session.add(PersonAllowedAdmin(person_id=person.id, user_id=int(uid)))
                    except (ValueError, TypeError):
                        pass

    person.updated_at = datetime.utcnow()
    session.add(person)

    existing_count = len(person.photos)
    for i, upload in enumerate(photos):
        if not upload.filename:
            continue
        if existing_count + i >= MAX_PHOTOS:
            break
        rel = _save_photo(person.id, upload)
        session.add(Photo(person_id=person.id, filename=rel, order=existing_count + i))
    session.commit()
    return RedirectResponse(f"/persons/{person.id}", status_code=303)


@router.post("/{person_id}/delete")
def delete_person(person_id: int, request: Request, session: Session = Depends(get_session)):
    """하드 삭제: Person + Photo + 디스크 파일 + PersonRevision 모두 제거.
    Encounter는 보존하되, 이 사람이 등장한 행은 FK를 NULL로 끊고
    public_id 스냅샷 + (deleted) 표시를 박아 이력 가독성을 유지한다.
    """
    from ..models import PersonRevision

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    _require_edit(person, request, session)

    snapshot = f"{person.public_id} (deleted)"
    related = session.exec(
        select(Encounter).where(
            (Encounter.person_a_id == person.id)
            | (Encounter.person_b_id == person.id)
        )
    ).all()
    for e in related:
        if e.person_a_id == person.id:
            e.person_a_id = None
            e.person_a_snapshot = snapshot
        if e.person_b_id == person.id:
            e.person_b_id = None
            e.person_b_snapshot = snapshot
        session.add(e)

    # PersonRevision은 그 사람의 이력이므로 함께 삭제 (FK 무결성 + 정책: 매물 삭제 시 완전 제거)
    revs = session.exec(
        select(PersonRevision).where(PersonRevision.person_id == person.id)
    ).all()
    for r in revs:
        session.delete(r)

    # 이 매물이 등장한 IntroductionRequest 도 정리 (양쪽 FK)
    reqs = session.exec(
        select(IntroductionRequest).where(
            (IntroductionRequest.my_person_id == person.id)
            | (IntroductionRequest.their_person_id == person.id)
        )
    ).all()
    for r in reqs:
        session.delete(r)

    # PersonAllowedAdmin (visibility allowlist) 정리
    paas = session.exec(
        select(PersonAllowedAdmin).where(PersonAllowedAdmin.person_id == person.id)
    ).all()
    for paa in paas:
        session.delete(paa)

    person_dir = UPLOAD_DIR / str(person.id)
    session.delete(person)
    session.commit()

    if person_dir.exists():
        shutil.rmtree(person_dir, ignore_errors=True)
    return RedirectResponse("/persons", status_code=303)


@router.post("/{person_id}/photos/{photo_id}/delete")
def delete_photo(
    person_id: int, photo_id: int, request: Request, session: Session = Depends(get_session)
):
    photo = session.get(Photo, photo_id)
    if not photo or photo.person_id != person_id:
        raise HTTPException(404, "Photo not found")
    person = session.get(Person, person_id)
    if person:
        _require_edit(person, request, session)
    file_path = UPLOAD_DIR / photo.filename
    session.delete(photo)
    session.commit()
    if file_path.exists():
        file_path.unlink(missing_ok=True)
    return RedirectResponse(f"/persons/{person_id}/edit", status_code=303)
