"""ActivityLog 헬퍼 — 라우터에서 한 줄로 행동 기록.

규칙:
  - log_activity 는 session.add 만 하고 commit 안 함 → 호출 측 트랜잭션에 묻어감.
    호출 측이 실패하면 로그도 같이 롤백되는 게 의도.
  - actor 가 None 이거나 LOCAL_ADMIN(id=0) 이어도 기록은 남김 (actor_display 만 'local').
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from ..models import ActivityLog, User


# 액션 → 사람이 읽을 한국어 라벨 (UI 표시 + 필터 드롭다운에 사용)
ACTION_LABEL: dict[str, str] = {
    "login": "🔑 로그인",
    "logout": "🚪 로그아웃",
    "user.register": "🆕 가입",
    "user.promote": "👑 권한 변경",
    "user.delete": "❌ 유저 삭제",
    "person.create": "🆕 매물 등록",
    "person.update": "✏️ 매물 수정",
    "person.delete": "🗑 매물 삭제",
    "person.star": "⭐ 즐겨찾기",
    "person.unstar": "☆ 즐겨찾기 해제",
    "blacklist.add": "🚫 블랙리스트 추가",
    "blacklist.remove": "↩️ 블랙리스트 해제",
    "encounter.create": "💞 만남 기록",
    "encounter.update": "🔄 만남 상태 변경",
    "encounter.delete": "🗑 만남 삭제",
    "request.send": "📨 소개 요청 보냄",
    "request.accept": "✅ 소개 요청 수락",
    "request.decline": "❌ 소개 요청 거절",
    "request.withdraw": "↩️ 소개 요청 취소",
}


def action_label(action: str) -> str:
    return ACTION_LABEL.get(action, action)


def log_activity(
    session: Session,
    actor: Optional[User],
    action: str,
    *,
    target_type: str = "",
    target_id: Optional[int] = None,
    summary: str = "",
) -> ActivityLog:
    row = ActivityLog(
        actor_user_id=(actor.id if actor and actor.id else None),
        actor_display=(actor.display_name if actor else ""),
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
    )
    session.add(row)
    return row
