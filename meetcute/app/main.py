import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from .auth import require_admin
from .config import PUBLIC_MODE, SECRET_IS_DEFAULT, SECRET_KEY, UPLOAD_DIR
from .database import get_session, init_db
from .models import Encounter, EncounterOutcome, Gender, Person, User
from .routers import auth, compatibility, encounters, manual, persons, requests as requests_router, settings as settings_router, users
from .services.activity import activity_for_persons
from .services.status import (
    PersonStatus,
    status_badge_class,
    status_label,
    statuses_for_persons,
)
from .templating import templates

logger = logging.getLogger("meetcute")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if SECRET_IS_DEFAULT:
        logger.warning(
            "MEETCUTE_SECRET 환경변수가 설정되지 않았습니다 (개발용 기본키 사용 중). "
            "운영 시 반드시 강한 임의값으로 지정하세요."
        )
    yield


app = FastAPI(title="meetcute", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=PUBLIC_MODE,  # 외부 노출 시 자동 HTTPS-only 쿠키
    max_age=60 * 60 * 24 * 14,  # 2주
)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# 인증 / 매뉴얼은 누구나 접근 가능
app.include_router(auth.router)
app.include_router(manual.router)

# 나머지는 관리자 전용
admin_dep = [Depends(require_admin)]
app.include_router(persons.router, dependencies=admin_dep)
app.include_router(encounters.router, dependencies=admin_dep)
app.include_router(compatibility.router, dependencies=admin_dep)
app.include_router(requests_router.router)  # 내부에서 require_admin 직접 사용
app.include_router(settings_router.router)  # 내부에서 require_login 직접 사용
app.include_router(users.router)  # 라우터 내부에서 require_admin 직접 의존


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    all_persons = session.exec(select(Person)).all()
    statuses = statuses_for_persons(session, all_persons)
    activities = activity_for_persons(session, all_persons)

    by_gender = {g: 0 for g in Gender}
    by_status = {s: 0 for s in PersonStatus}
    for p in all_persons:
        by_gender[p.gender] += 1
        s = statuses.get(p.id)
        if s:
            by_status[s] += 1

    # "오래 잠자는 매물": AVAILABLE 이면서 30일+ 미활동(또는 한 번도 만남 X). 최대 6명.
    dormant_threshold = 30
    dormant_candidates = []
    for p in all_persons:
        s = statuses.get(p.id)
        if s != PersonStatus.AVAILABLE:
            continue
        a = activities.get(p.id)
        if a is None:
            continue
        if a.never_met or (a.days_dormant or 0) >= dormant_threshold:
            dormant_candidates.append((p, a))
    # 미활동 일수 큰 순 (never_met은 created_at 오래된 순)
    from datetime import datetime as _dt

    def _dormant_key(item):
        p, a = item
        if a.never_met:
            return (
                10**9,
                (_dt.utcnow() - p.created_at).days,
            )
        return (a.days_dormant or 0, 0)

    dormant_candidates.sort(key=_dormant_key, reverse=True)
    dormant_persons = dormant_candidates[:6]

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
        select(Encounter)
        .order_by(Encounter.met_on.desc(), Encounter.id.desc())
        .limit(8)
    ).all()

    person_map = {p.id: p for p in all_persons}

    from .routers.encounters import OUTCOME_BADGE, OUTCOME_LABEL

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
            "dormant_persons": dormant_persons,
            "dormant_threshold": dormant_threshold,
            "current_user": current_user,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "OUTCOME_LABEL": OUTCOME_LABEL,
            "OUTCOME_BADGE": OUTCOME_BADGE,
        },
    )
