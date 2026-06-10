import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path as _Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from sqlalchemy.orm import defer
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import require_admin
from .bot import bot_poll_loop
from .config import AUTH_ENABLED, BASE_DIR, PUBLIC_MODE, SECRET_IS_DEFAULT, SECRET_KEY, UPLOAD_DIR
from .reminders import reminder_loop
from .url_watcher import url_watcher_loop
from .database import get_session, init_db
from .models import Encounter, EncounterOutcome, Gender, Person, User
from .routers import activity, auth, blacklist, chat, compatibility, encounters, manual, persons, requests as requests_router, settings as settings_router, users
from .services.activity import activity_for_persons
from .services.status import (
    PersonStatus,
    grouped_encounters_for_persons,
    status_badge_class,
    status_label,
    statuses_for_persons,
)
from .services.visibility import allowed_set_for_user, can_see_person
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
    # 서버 시작 알림 (콜드 부팅 시 1회. 30분 cooldown 으로 hot reload 도배 방지).
    # 텔레그램 API 호출이라 별도 thread 로 (uvicorn 시작 안 막게).
    try:
        from .startup_notice import send_startup_notification
        asyncio.create_task(asyncio.to_thread(send_startup_notification))
    except Exception as e:
        logger.warning(f"startup notice failed to schedule: {e}")
    reminder_task = asyncio.create_task(reminder_loop())
    bot_task = asyncio.create_task(bot_poll_loop())
    url_task = asyncio.create_task(url_watcher_loop())
    try:
        yield
    finally:
        for t in (reminder_task, bot_task, url_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="meetcute", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=PUBLIC_MODE,  # 외부 노출 시 자동 HTTPS-only 쿠키
    max_age=60 * 60 * 24 * 14,  # 2주
)
# 응답 gzip 압축 (1KB 이상). Tailwind class 가 매우 반복적이라 HTML 페이지가
# 보통 5~10배 줄어듦 — 탭 이동 시 HTML 전송 시간이 가장 큰 병목이었음.
app.add_middleware(GZipMiddleware, minimum_size=1000)
_STATIC_DIR = _Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


_UPLOAD_ROOT = UPLOAD_DIR.resolve()


@app.get("/uploads/{rest:path}")
def serve_upload(rest: str, current_user=Depends(require_admin)):
    """업로드된 사진은 로그인된 마담뚜만 접근 가능 (URL 추측 + 유출 방어).
    AUTH=off 모드면 require_admin 이 LOCAL_ADMIN 으로 통과시킴.
    Cache-Control 헤더로 브라우저 캐시 — 같은 사진 재요청 시 즉시.

    옛 사진 호환: '_thumb' 가 붙은 경로인데 파일이 없으면 원본으로 폴백.
    썸네일 기능 도입 전에 업로드된 사진은 _thumb 파일이 없음."""
    # path traversal 방어
    target = (UPLOAD_DIR / rest).resolve()
    try:
        target.relative_to(_UPLOAD_ROOT)
    except ValueError:
        raise HTTPException(404)
    if not target.is_file():
        # _thumb 가 없으면 원본 시도 (옛 사진 호환)
        from pathlib import PurePosixPath
        p = PurePosixPath(rest)
        if p.stem.endswith("_thumb"):
            fallback = str(p.with_name(p.stem[:-len("_thumb")] + p.suffix))
            target = (UPLOAD_DIR / fallback).resolve()
            try:
                target.relative_to(_UPLOAD_ROOT)
            except ValueError:
                raise HTTPException(404)
            if not target.is_file():
                raise HTTPException(404)
        else:
            raise HTTPException(404)
    return FileResponse(
        target,
        headers={
            # private = CDN 등 공유 캐시 차단, 브라우저만 캐시
            # immutable = 파일명이 uuid 라 절대 안 바뀜 → 새로고침해도 재요청 안 함
            "Cache-Control": "private, max-age=86400, immutable",
        },
    )

# 인증 / 매뉴얼은 누구나 접근 가능
app.include_router(auth.router)
app.include_router(manual.router)

# 나머지는 관리자 전용
admin_dep = [Depends(require_admin)]
app.include_router(persons.router, dependencies=admin_dep)
app.include_router(encounters.router, dependencies=admin_dep)
app.include_router(compatibility.router, dependencies=admin_dep)
app.include_router(blacklist.router, dependencies=admin_dep)
app.include_router(requests_router.router)  # 내부에서 require_admin 직접 사용
app.include_router(chat.router)  # 마담뚜 임시 대화방. 내부에서 require_admin 직접 사용
app.include_router(settings_router.router)  # 내부에서 require_login 직접 사용
app.include_router(users.router)  # 라우터 내부에서 require_admin 직접 의존
app.include_router(activity.router)  # 책임자 전용. 라우터 내부에서 require_owner


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    # 대시보드는 ideal_type/notes 안 씀 → defer 로 복호화 비용 절감
    all_persons = session.exec(
        select(Person).options(defer(Person.ideal_type), defer(Person.notes))
    ).all()
    # 비공개(RESTRICTED) 매물은 권한 없는 마담뚜에게 신규/잠자는 리스트와
    # 만남 카드에서 가려진다. 카운트는 시스템 전체 기준 유지.
    allowed_visible = allowed_set_for_user(session, current_user)
    visible_person_ids = {
        p.id for p in all_persons
        if can_see_person(p, current_user, allowed_set=allowed_visible)
    }
    # Encounter 한 번만 가져와서 status/activity 양쪽에 재사용
    grouped = grouped_encounters_for_persons(session, all_persons)
    statuses = statuses_for_persons(session, all_persons, grouped=grouped)
    activities = activity_for_persons(session, all_persons, grouped=grouped)

    by_gender = {g: 0 for g in Gender}
    by_status = {s: 0 for s in PersonStatus}
    for p in all_persons:
        by_gender[p.gender] += 1
        s = statuses.get(p.id)
        if s:
            by_status[s] += 1

    # "🆕 신규 매물": 등록 7일 이내. 신규 풀에 있는 동안은 "오래 잠자는" 에서 제외.
    # "😴 오래 잠자는 매물": AVAILABLE + 7일+ 등록 + 30일+ 미활동(또는 한 번도 만남 X).
    from datetime import datetime as _dt, timedelta as _td

    dormant_threshold = 30
    new_threshold_days = 7
    now = _dt.utcnow()

    new_persons: list = []
    dormant_candidates: list = []
    for p in all_persons:
        # 비공개 매물은 권한 없는 마담뚜 화면에 안 띄움 (사진/public_id 보호)
        if p.id not in visible_person_ids:
            continue
        age_days = (now - p.created_at).days
        s = statuses.get(p.id)
        a = activities.get(p.id)
        if age_days < new_threshold_days:
            new_persons.append((p, a))
            continue  # 신규 풀에 있으면 잠자는 풀 제외
        if s != PersonStatus.AVAILABLE or a is None:
            continue
        if a.never_met or (a.days_dormant or 0) >= dormant_threshold:
            dormant_candidates.append((p, a))

    # 신규는 최신순 (가장 최근 등록이 위)
    new_persons.sort(key=lambda x: x[0].created_at, reverse=True)
    new_persons = new_persons[:6]

    def _dormant_key(item):
        p, a = item
        if a.never_met:
            return (10**9, (now - p.created_at).days)
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
    # 진행중/최근 만남 카드에서 RESTRICTED 매물 마스킹용
    visible = {pid: (pid in visible_person_ids) for pid in person_map}

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
            "visible": visible,
            "new_persons": new_persons,
            "new_threshold_days": new_threshold_days,
            "dormant_persons": dormant_persons,
            "dormant_threshold": dormant_threshold,
            "current_user": current_user,
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "OUTCOME_LABEL": OUTCOME_LABEL,
            "OUTCOME_BADGE": OUTCOME_BADGE,
        },
    )
