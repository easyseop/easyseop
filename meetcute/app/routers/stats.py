"""소개팅/매칭 통계 페이지.

- 전체 핵심 지표 + 결과 분포 + 월별 추이: 모든 마담뚜 열람 가능 (require_admin).
- 마담뚜별 성과 표: 책임자(is_owner) 에게만 노출 (개인 성과 민감) → 템플릿에서 분기.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from ..auth import require_admin
from ..config import AUTH_ENABLED
from ..database import get_session
from ..models import EncounterOutcome, IntroRequestStatus, User
from ..routers.encounters import OUTCOME_BADGE, OUTCOME_LABEL
from ..services.stats import compute_stats
from ..templating import templates

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_class=HTMLResponse)
def stats_page(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    data = compute_stats(session)
    return templates.TemplateResponse(
        request,
        "stats/index.html",
        {
            "current_user": current_user,
            "OUTCOME_LABEL": OUTCOME_LABEL,
            "OUTCOME_BADGE": OUTCOME_BADGE,
            "EncounterOutcome": EncounterOutcome,
            "IntroRequestStatus": IntroRequestStatus,
            **data,
        },
    )
