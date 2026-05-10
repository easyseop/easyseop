from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, func

from .config import UPLOAD_DIR
from .database import get_session, init_db
from .models import Encounter, EncounterOutcome, Gender, Person
from .routers import compatibility, encounters, manual, persons
from .services.status import (
    PersonStatus,
    statuses_for_persons,
    status_badge_class,
    status_label,
)
from .templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="meetcute", lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.include_router(persons.router)
app.include_router(encounters.router)
app.include_router(compatibility.router)
app.include_router(manual.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    all_persons = session.exec(select(Person)).all()
    statuses = statuses_for_persons(session, all_persons)

    by_gender = {g: 0 for g in Gender}
    by_status = {s: 0 for s in PersonStatus}
    for p in all_persons:
        by_gender[p.gender] += 1
        s = statuses.get(p.id)
        if s:
            by_status[s] += 1

    # 진행 중인 매칭: active outcome encounters, 최신순
    active_encs = session.exec(
        select(Encounter)
        .where(
            (Encounter.outcome == EncounterOutcome.PENDING)
            | (Encounter.outcome == EncounterOutcome.CONTINUING)
        )
        .order_by(Encounter.met_on.desc())
        .limit(10)
    ).all()

    recent_encs = session.exec(
        select(Encounter).order_by(Encounter.met_on.desc(), Encounter.id.desc()).limit(8)
    ).all()

    person_map = {p.id: p for p in all_persons}

    from .routers.encounters import OUTCOME_LABEL, OUTCOME_BADGE
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": {
                "total": len(all_persons),
                "male": by_gender[Gender.M],
                "female": by_gender[Gender.F],
                "other": by_gender[Gender.OTHER],
                "available": by_status[PersonStatus.AVAILABLE],
                "in_progress": by_status[PersonStatus.IN_PROGRESS],
                "matched": by_status[PersonStatus.MATCHED],
            },
            "active_encs": active_encs,
            "recent_encs": recent_encs,
            "person_map": person_map,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "OUTCOME_LABEL": OUTCOME_LABEL,
            "OUTCOME_BADGE": OUTCOME_BADGE,
        },
    )
