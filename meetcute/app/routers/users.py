"""관리자 전용 유저 관리. 라우터 레벨에서 require_admin 적용됨."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import require_admin
from ..database import get_session
from ..models import User
from ..templating import templates

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    users = session.exec(select(User).order_by(User.created_at)).all()
    return templates.TemplateResponse(
        request,
        "users/list.html",
        {"users": users, "current_user": current_user},
    )


@router.post("/{user_id}/toggle-admin")
def toggle_admin(
    user_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")

    # 마지막 관리자가 자기 자신을 강등하면 시스템에 관리자 0명 → 차단
    if target.is_admin and target.id == current_user.id:
        admin_count = len(
            session.exec(select(User).where(User.is_admin == True)).all()  # noqa: E712
        )
        if admin_count <= 1:
            raise HTTPException(
                400, "마지막 관리자는 강등할 수 없습니다 (먼저 다른 사람을 관리자로)"
            )

    target.is_admin = not target.is_admin
    session.add(target)
    session.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/{user_id}/delete")
def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.id == current_user.id:
        raise HTTPException(400, "자기 자신은 삭제할 수 없습니다")
    if target.is_admin:
        admin_count = len(
            session.exec(select(User).where(User.is_admin == True)).all()  # noqa: E712
        )
        if admin_count <= 1:
            raise HTTPException(400, "마지막 관리자는 삭제할 수 없습니다")
    session.delete(target)
    session.commit()
    return RedirectResponse("/users", status_code=303)
