"""소개팅/매칭 통계 페이지.

- 전체 핵심 지표 + 결과 분포 + 월별 추이: 모든 마담뚜 열람 가능 (require_admin).
- 마담뚜별 성과 표: 책임자(is_owner) 에게만 노출 (개인 성과 민감) → 템플릿에서 분기.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
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


@router.post("/dormant-now")
def send_dormant_now(
    request: Request,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """'2주째 방치 매물' 알림을 지금 즉시 1회 발송 (책임자 전용).
    baseline·쿨다운 무시하고 현재 방치된 소개가능 매물 다이제스트를 전 마담뚜에게."""
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    if not current_user.is_owner:
        raise HTTPException(403, "책임자만 실행할 수 있습니다")
    from ..reminders import _send_dormant_person_reminders
    n = _send_dormant_person_reminders(force=True)
    if n > 0:
        msg = f"방치 매물 알림을 {n}명의 마담뚜에게 보냈어요"
    else:
        msg = "지금 2주+ 방치된 '소개 가능' 매물이 없거나 텔레그램 미설정 — 발송 0"
    return RedirectResponse("/stats?ok=" + quote(msg), status_code=303)


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
