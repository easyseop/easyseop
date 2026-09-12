"""승격 안 된(대기) 계정의 접근 범위 고정 — 보안 회귀 방지.

가입만 하고 마담뚜로 승격 안 된 계정이:
  - 매물/만남/요청/대화/통계/유저/로그/사진 에 접근 못 함
  - /settings 는 들어가지만 '본인 설정' 만 보이고, 다른 사람 텔레그램
    chat_id·이름(자동 감지)·DB 통계는 안 보임
"""
import pytest


def _register(client, email):
    client.cookies.clear()
    client.post("/auth/register", data={
        "email": email, "password": "pw12345678", "password_confirm": "pw12345678",
    }, follow_redirects=False)


def _login(client, email):
    client.cookies.clear()
    client.post("/auth/login", data={"email": email, "password": "pw12345678"},
                follow_redirects=False)


@pytest.fixture
def pending_user(client, session):
    """boss(책임자) + pending(가입만 함, 승격 X). pending 으로 로그인된 상태로 반환."""
    _register(client, "boss@x.com")      # 첫 가입 = 책임자
    _register(client, "pending@x.com")   # 두 번째 = 권한 없음
    _login(client, "pending@x.com")
    return True


ADMIN_ONLY_PATHS = [
    "/",                 # 대시보드
    "/persons",
    "/persons/new",
    "/encounters",
    "/encounters/new",
    "/compatibility",
    "/blacklist",
    "/requests",
    "/requests/new",
    "/chat",
    "/stats",
    "/users",            # 책임자 전용
    "/activity",         # 책임자 전용
]


@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_pending_user_blocked_from_admin_pages(client, pending_user, path):
    """비승격 계정은 매칭 관련 페이지 어디도 200 으로 못 받음."""
    r = client.get(path, follow_redirects=False)
    assert r.status_code != 200, f"{path} 가 비승격 계정에 열려 있음!"
    # 303(리다이렉트) 또는 403 이어야 함
    assert r.status_code in (303, 403), f"{path} → 예상 밖 상태 {r.status_code}"


def test_pending_user_cannot_read_uploads(client, session, pending_user):
    """사진 URL 을 알아도 비승격 계정은 못 받음."""
    r = client.get("/uploads/1/whatever.jpg", follow_redirects=False)
    assert r.status_code != 200
    assert r.status_code in (303, 403, 404)


def test_pending_user_settings_hides_others_telegram(client, session, pending_user, monkeypatch):
    """자동 감지(다른 사람 chat_id/이름)는 비승격 계정에 노출 X."""
    from app.routers import settings as settings_router

    # 감지되면 노출될 가짜 데이터 — 호출 자체가 막혀야 함
    called = []

    def _fake_detect():
        called.append(1)
        return [{"id": "999", "name": "다른마담뚜"}], ""

    monkeypatch.setattr(settings_router, "_detect_chats", _fake_detect)

    r = client.get("/settings?detect=1")
    assert r.status_code == 200          # 본인 설정 페이지는 접근 가능
    assert called == [], "비승격 계정이 _detect_chats 를 호출함!"
    assert "다른마담뚜" not in r.text
    assert "999" not in r.text
    assert "자동 감지" not in r.text     # 버튼도 안 보여야


def test_pending_user_settings_hides_db_stats(client, session, pending_user):
    """DB/시스템 통계는 마담뚜 전용."""
    r = client.get("/settings")
    assert r.status_code == 200
    assert "데이터 / 시스템 상태" not in r.text


def test_admin_still_sees_detect(client, session, monkeypatch):
    """마담뚜(책임자)는 자동 감지 그대로 사용 가능 — 기능 회귀 없음 확인."""
    from app.routers import settings as settings_router

    _register(client, "boss@x.com")
    _login(client, "boss@x.com")
    monkeypatch.setattr(
        settings_router, "_detect_chats",
        lambda: ([{"id": "999", "name": "내계정"}], ""),
    )
    r = client.get("/settings?detect=1")
    assert r.status_code == 200
    assert "999" in r.text
    assert "내계정" in r.text
