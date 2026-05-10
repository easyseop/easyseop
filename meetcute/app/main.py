from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, func

from .config import UPLOAD_DIR
from .database import get_session, init_db
from .models import Gender, Person
from .routers import persons
from .templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="meetcute", lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.include_router(persons.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    total = session.exec(select(func.count()).select_from(Person)).one()
    by_gender = {
        g: session.exec(
            select(func.count()).select_from(Person).where(Person.gender == g)
        ).one()
        for g in Gender
    }
    stats = {
        "total": total,
        "male": by_gender[Gender.M],
        "female": by_gender[Gender.F],
        "other": by_gender[Gender.OTHER],
    }
    return templates.TemplateResponse(
        request, "index.html", {"stats": stats}
    )
