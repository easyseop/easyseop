import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..config import UPLOAD_DIR
from ..database import get_session, next_public_id
from ..models import Encounter, Gender, Person, Photo
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
    session: Session = Depends(get_session),
):
    stmt = select(Person).order_by(Person.created_at.desc())
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
    statuses = statuses_for_persons(session, persons)
    return templates.TemplateResponse(
        request,
        "persons/list.html",
        {
            "persons": persons,
            "statuses": statuses,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "gender": gender,
            "q": q or "",
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_person_form(request: Request):
    return templates.TemplateResponse(
        request,
        "persons/form.html",
        {"person": None, "Gender": Gender},
    )


@router.post("")
async def create_person(
    gender: Gender = Form(...),
    age: int = Form(...),
    location: str = Form(...),
    workplace: str = Form(...),
    height_cm: int = Form(...),
    ideal_type: str = Form(""),
    notes: str = Form(""),
    alias: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    public_id = next_public_id(session, gender)
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
    return templates.TemplateResponse(
        request,
        "persons/form.html",
        {"person": person, "Gender": Gender},
    )


@router.post("/{person_id}")
async def update_person(
    person_id: int,
    age: int = Form(...),
    location: str = Form(...),
    workplace: str = Form(...),
    height_cm: int = Form(...),
    ideal_type: str = Form(""),
    notes: str = Form(""),
    alias: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    person.age = age
    person.location = location
    person.workplace = workplace
    person.height_cm = height_cm
    person.ideal_type = ideal_type
    person.notes = notes
    person.alias = alias
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
    """하드 삭제: Person + Photo + 디스크 파일 제거.
    Encounter는 보존하되, 이 사람이 등장한 행은 FK를 NULL로 끊고
    public_id 스냅샷 + (deleted) 표시를 박아 이력 가독성을 유지한다.
    """
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
