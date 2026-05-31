"""책임자 (is_owner) 전용 유저 관리. 권한 토글 / 삭제 / 비번 강제 리셋."""
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import hash_password, require_owner
from ..database import get_session
from ..models import User
from ..services.activity_log import log_activity
from ..templating import templates

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    current_user: User = Depends(require_owner),
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
    current_user: User = Depends(require_owner),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")

    # 마지막 마담뚜가 자기 자신을 강등하면 시스템에 관리자 0명 → 차단
    if target.is_admin and target.id == current_user.id:
        admin_count = len(
            session.exec(select(User).where(User.is_admin == True)).all()  # noqa: E712
        )
        if admin_count <= 1:
            raise HTTPException(
                400, "마지막 마담뚜는 강등할 수 없습니다 (먼저 다른 사람을 관리자로)"
            )

    target.is_admin = not target.is_admin
    session.add(target)
    log_activity(
        session, current_user, "user.promote",
        target_type="user", target_id=target.id,
        summary=f"{target.display_name} → 마담뚜 {'활성화' if target.is_admin else '강등'}",
    )
    session.commit()
    return RedirectResponse("/users", status_code=303)


def _generate_temp_password() -> str:
    """임시 비번 — 책임자가 외부 채널로 전달용. 한 번 보여주고 사라짐.
    구성: 영문(대소문자) + 숫자, 14자. URL-safe 문자만 (구두점 안 섞음 — 받아쓰기 좋게)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # 헷갈리는 0,O,1,l,I 제외
    return "".join(secrets.choice(alphabet) for _ in range(14))


@router.post("/{user_id}/reset-password", response_class=HTMLResponse)
def reset_password(
    request: Request,
    user_id: int,
    current_user: User = Depends(require_owner),
    session: Session = Depends(get_session),
):
    """책임자가 다른 마담뚜 비밀번호 강제 리셋. 임시 비번 한 번 표시.
    텔레그램 미연동 마담뚜용 — 임시 비번은 외부 채널(전화/SMS/대면)로 전달."""
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    temp = _generate_temp_password()
    target.password_hash = hash_password(temp)
    session.add(target)
    log_activity(
        session, current_user, "user.password_force_reset",
        target_type="user", target_id=target.id,
        summary=f"{target.display_name} 비번 강제 리셋 (책임자)",
    )
    session.commit()
    # 임시 비번은 한 번만 노출. 페이지 새로고침 X → URL 에 안 박음.
    return templates.TemplateResponse(
        request,
        "users/temp_password.html",
        {"target": target, "temp_password": temp, "current_user": current_user},
    )


@router.post("/{user_id}/delete")
def delete_user(
    user_id: int,
    current_user: User = Depends(require_owner),
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
            raise HTTPException(400, "마지막 마담뚜는 삭제할 수 없습니다")
    target_id = target.id
    target_name = target.display_name
    session.delete(target)
    log_activity(
        session, current_user, "user.delete",
        target_type="user", target_id=target_id,
        summary=f"{target_name} 삭제",
    )
    session.commit()
    return RedirectResponse("/users", status_code=303)
