import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"

# ─── DB ─────────────────────────────────────────────────────────────────────
# 기본은 SQLite — 1인 로컬 사용에 충분, 별도 설치 필요 없음.
# MySQL/PostgreSQL 쓸 거면 환경변수 MEETCUTE_DB_URL 로:
#   MEETCUTE_DB_URL="mysql+pymysql://user:pass@host:3306/meetcute?charset=utf8mb4"
#   MEETCUTE_DB_URL="postgresql+psycopg2://user:pass@host:5432/meetcute"
DATABASE_URL = os.getenv(
    "MEETCUTE_DB_URL",
    f"sqlite:///{DATA_DIR / 'meetcute.db'}",
)

# 세션 쿠키 서명용. 운영 시 환경변수 MEETCUTE_SECRET을 반드시 지정하세요.
SECRET_KEY = os.getenv("MEETCUTE_SECRET", "dev-secret-change-me")
SECRET_IS_DEFAULT = SECRET_KEY == "dev-secret-change-me"

# 인증 켜고 끄기. 기본은 OFF (혼자 로컬에서 쓰는 용도).
# 다시 켜고 싶을 때: MEETCUTE_AUTH=on 으로 띄우면 됨.
AUTH_ENABLED = os.getenv("MEETCUTE_AUTH", "").strip().lower() in ("on", "1", "true", "yes")

# 외부 공개 모드 (cloudflared 등 터널 뒤에서 돌릴 때):
#   - 세션 쿠키 HTTPS 전용 (Secure 플래그)
#   - AUTH 자동 강제 ON
#   - UI 에 공개 모드 배너
PUBLIC_MODE = os.getenv("MEETCUTE_PUBLIC", "").strip().lower() in ("on", "1", "true", "yes")
if PUBLIC_MODE:
    AUTH_ENABLED = True  # 공개 모드면 인증 강제

# 외부에서 접근하는 절대 URL (예: https://meetcute.yourdomain.com).
# 텔레그램 알림에 클릭 가능한 링크 박을 때 사용. 비워두면 상대경로만.
PUBLIC_URL = os.getenv("MEETCUTE_PUBLIC_URL", "").strip().rstrip("/")

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
