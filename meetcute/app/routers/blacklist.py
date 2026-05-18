"""블랙리스트 페어 관리 — "A와 B는 절대 안 됨" 마킹.

용도:
  - 이미 만났는데 안 맞은 페어
  - 서로 아는 사이 (친척, 친구)
  - 한쪽이 명시적으로 거부한 페어

영향:
  - 호환성 페이지 (/compatibility) 에서 자동 경고
  - 만남 기록 / 소개 요청 생성 시 경고 (하지만 차단은 안 함 — 마담뚜 판단)
  - 활동 로그에 add/remove 기록
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import get_current_user, require_admin
from ..database import get_session
from ..models import BlacklistedPair, Person, User
from ..services.activity_log import log_activity
from ..templating import templates

router = APIRouter(prefix="/blacklist", tags=["blacklist"])


def _canonical(a_id: int, b_id: int) -> tuple[int, int]:
    """페어를 정렬된 형태로 — (작은 id, 큰 id)."""
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def is_blacklisted(session: Session, a_id: int, b_id: int) -> bool:
    """헬퍼: a-b 페어가 블랙리스트에 있는지. 다른 라우터에서 import 해서 사용."""
    if not a_id or not b_id or a_id == b_id:
        return False
    a, b = _canonical(a_id, b_id)
    row = session.exec(
        select(BlacklistedPair).where(
            BlacklistedPair.person_a_id == a,
            BlacklistedPair.person_b_id == b,
        )
    ).first()
    return row is not None


@router.get("", response_class=HTMLResponse)
def list_blacklist(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    pairs = session.exec(
        select(BlacklistedPair).order_by(BlacklistedPair.created_at.desc())
    ).all()
    # 페어에 등장하는 모든 person_id 한 번에 fetch
    pids = set()
    for p in pairs:
        pids.add(p.person_a_id); pids.add(p.person_b_id)
    persons: dict[int, Person] = {}
    if pids:
        rows = session.exec(select(Person).where(Person.id.in_(list(pids)))).all()
        persons = {p.id: p for p in rows}
    all_persons = session.exec(select(Person).order_by(Person.public_id)).all()
    return templates.TemplateResponse(
        request,
        "blacklist/list.html",
        {
            "pairs": pairs,
            "persons": persons,
            "all_persons": all_persons,
            "current_user": current_user,
        },
    )


@router.post("")
def add_to_blacklist(
    request: Request,
    person_a_id: int = Form(...),
    person_b_id: int = Form(...),
    reason: str = Form(""),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if person_a_id == person_b_id:
        raise HTTPException(400, "같은 매물끼리는 블랙리스트 안 됩니다")
    a = session.get(Person, person_a_id)
    b = session.get(Person, person_b_id)
    if not a or not b:
        raise HTTPException(404, "매물을 찾을 수 없습니다")

    a_id, b_id = _canonical(person_a_id, person_b_id)
    existing = session.exec(
        select(BlacklistedPair).where(
            BlacklistedPair.person_a_id == a_id,
            BlacklistedPair.person_b_id == b_id,
        )
    ).first()
    if existing:
        return RedirectResponse(
            "/blacklist?err=" + "이미+블랙리스트에+있습니다", status_code=303
        )

    pair = BlacklistedPair(
        person_a_id=a_id,
        person_b_id=b_id,
        reason=reason.strip(),
        created_by_user_id=current_user.id if current_user and current_user.id else None,
        created_by_display=current_user.display_name if current_user else "",
    )
    session.add(pair)
    a_pub = a.public_id; b_pub = b.public_id
    log_activity(
        session, current_user, "blacklist.add",
        target_type="person", target_id=a_id,
        summary=f"{a_pub} ✕ {b_pub}" + (f" — {reason.strip()}" if reason.strip() else ""),
    )
    session.commit()
    return RedirectResponse("/blacklist?ok=" + "추가됨", status_code=303)


@router.post("/{a_id}_{b_id}/delete")
def remove_from_blacklist(
    a_id: int,
    b_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    aa, bb = _canonical(a_id, b_id)
    pair = session.exec(
        select(BlacklistedPair).where(
            BlacklistedPair.person_a_id == aa,
            BlacklistedPair.person_b_id == bb,
        )
    ).first()
    if not pair:
        raise HTTPException(404, "블랙리스트 페어를 찾을 수 없습니다")
    a = session.get(Person, aa)
    b = session.get(Person, bb)
    session.delete(pair)
    log_activity(
        session, current_user, "blacklist.remove",
        target_type="person", target_id=aa,
        summary=f"{a.public_id if a else aa} ✕ {b.public_id if b else bb}",
    )
    session.commit()
    return RedirectResponse("/blacklist?ok=" + "해제됨", status_code=303)
