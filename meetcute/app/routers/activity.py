"""책임자 전용 활동 로그 페이지."""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from ..auth import require_owner
from ..database import get_session
from ..models import ActivityLog, User
from ..services.activity_log import ACTION_LABEL, action_label
from ..templating import templates

router = APIRouter(prefix="/activity", tags=["activity"])

PAGE_SIZE = 50


@router.get("", response_class=HTMLResponse)
def list_activity(
    request: Request,
    actor: Optional[int] = None,
    action: Optional[str] = None,
    page: int = 1,
    current_user: User = Depends(require_owner),
    session: Session = Depends(get_session),
):
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc())
    if actor:
        stmt = stmt.where(ActivityLog.actor_user_id == actor)
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    page = max(1, page)
    rows = session.exec(stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)).all()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    actors_map: dict[int, User] = {}
    if actor_ids:
        users = session.exec(select(User).where(User.id.in_(list(actor_ids)))).all()
        actors_map = {u.id: u for u in users}

    # 필터 드롭다운용 마담뚜 전체 목록
    all_users = session.exec(select(User).order_by(User.created_at)).all()

    return templates.TemplateResponse(
        request,
        "activity/list.html",
        {
            "rows": rows,
            "actors_map": actors_map,
            "all_users": all_users,
            "ACTION_LABEL": ACTION_LABEL,
            "action_label": action_label,
            "actor": actor,
            "action": action or "",
            "page": page,
            "has_next": has_next,
            "current_user": current_user,
        },
    )
