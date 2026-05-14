import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import AUTH_ENABLED, UPLOAD_DIR
from ..database import get_session, next_public_id
from ..models import Encounter, Gender, IntroRequestStatus, IntroductionRequest, Person, Photo, User
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


def _save_photo(person_id: int, upload: UploadFile) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    person_dir = UPLOAD_DIR / str(person_id)
    person_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    dest = person_dir / name
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return f"{person_id}/{name}"


@router.get("", response_class=HTMLResponse)
def list_persons(
    request: Request,
    gender: Optional[Gender] = None,
    q: Optional[str] = None,
    activity: Optional[str] = None,  # 'never' | 'dormant' | 'active' | None
    owner: Optional[str] = None,     # 'mine' | 'others' | 'unassigned' | None (전체)
    sort: Optional[str] = None,      # 'recent_activity' | 'dormant' | 'created' (default)
    session: Session = Depends(get_session),
):
    current_user = get_current_user(request, session)
    stmt = select(Person)
    if gender:
        stmt = stmt.where(Person.gender == gender)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Person.public_id.like(like))
            | (Person.alias.like(like))
            | (Person.location.like(like))
            | (Person.workplace.like(like))
        )
    persons = session.exec(stmt).all()

    # owner 필터 (AUTH=on 일 때만 의미)
    if AUTH_ENABLED and current_user and current_user.id:
        if owner == "mine":
            persons = [p for p in persons if p.owner_user_id == current_user.id]
        elif owner == "others":
            persons = [p for p in persons
                       if p.owner_user_id and p.owner_user_id != current_user.id]
        elif owner == "unassigned":
            persons = [p for p in persons if p.owner_user_id is None]

    # owner 정보 매핑 (목록 카드에 표시)
    owner_map: dict[int, User] = {}
    if AUTH_ENABLED:
        owner_ids = {p.owner_user_id for p in persons if p.owner_user_id}
        if owner_ids:
            rows = session.exec(select(User).where(User.id.in_(list(owner_ids)))).all()
            owner_map = {u.id: u for u in rows}

    statuses = statuses_for_persons(session, persons)
    stats = activity_for_persons(session, persons)

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

    return templates.TemplateResponse(
        request,
        "persons/list.html",
        {
            "persons": persons,
            "statuses": statuses,
            "stats": stats,
            "owner_map": owner_map,
            "current_user": current_user,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "gender": gender,
            "q": q or "",
            "activity": activity or "",
            "owner": owner or "",
            "sort": sort or "",
        },
    )


def _admins(session: Session) -> list[User]:
    return session.exec(select(User).where(User.is_admin == True).order_by(User.email)).all()  # noqa: E712


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

    # owner 결정: AUTH 켜져있고 폼에서 값이 오면 그대로, 비어있으면 현재 유저, AUTH 꺼져있으면 None
    resolved_owner_id: Optional[int] = None
    if AUTH_ENABLED:
        if owner_user_id.strip():
            try:
                resolved_owner_id = int(owner_user_id)
            except ValueError:
                resolved_owner_id = None
        else:
            cu = get_current_user(request, session)
            resolved_owner_id = cu.id if cu and cu.id else None

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

    return RedirectResponse(f"/persons/{person.id}", status_code=303)


@router.get("/{person_id}", response_class=HTMLResponse)
def person_detail(
    person_id: int, request: Request, session: Session = Depends(get_session)
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
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
    # "내 매물 → 다른 admin 소유의 매물에 소개 요청 보내기" CTA 노출 조건
    can_send_intro_request = bool(
        AUTH_ENABLED and current_user and current_user.id
        and person.owner_user_id
        and person.owner_user_id != current_user.id
    )

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
    current_user = get_current_user(request, session)
    return templates.TemplateResponse(
        request,
        "persons/form.html",
        {
            "person": person,
            "Gender": Gender,
            "admins": _admins(session) if AUTH_ENABLED else [],
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
    photos: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")

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
def delete_person(person_id: int, session: Session = Depends(get_session)):
    """하드 삭제: Person + Photo + 디스크 파일 + PersonRevision 모두 제거.
    Encounter는 보존하되, 이 사람이 등장한 행은 FK를 NULL로 끊고
    public_id 스냅샷 + (deleted) 표시를 박아 이력 가독성을 유지한다.
    """
    from ..models import PersonRevision

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")

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

    person_dir = UPLOAD_DIR / str(person.id)
    session.delete(person)
    session.commit()

    if person_dir.exists():
        shutil.rmtree(person_dir, ignore_errors=True)
    return RedirectResponse("/persons", status_code=303)


@router.post("/{person_id}/photos/{photo_id}/delete")
def delete_photo(
    person_id: int, photo_id: int, session: Session = Depends(get_session)
):
    photo = session.get(Photo, photo_id)
    if not photo or photo.person_id != person_id:
        raise HTTPException(404, "Photo not found")
    file_path = UPLOAD_DIR / photo.filename
    session.delete(photo)
    session.commit()
    if file_path.exists():
        file_path.unlink(missing_ok=True)
    return RedirectResponse(f"/persons/{person_id}/edit", status_code=303)
