import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = DATA_DIR / "meetcute.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# 세션 쿠키 서명용. 운영 시 환경변수 MEETCUTE_SECRET을 반드시 지정하세요.
SECRET_KEY = os.getenv("MEETCUTE_SECRET", "dev-secret-change-me")
SECRET_IS_DEFAULT = SECRET_KEY == "dev-secret-change-me"

# 인증 켜고 끄기. 기본은 OFF (혼자 로컬에서 쓰는 용도).
# 다시 켜고 싶을 때: MEETCUTE_AUTH=on 으로 띄우면 됨.
AUTH_ENABLED = os.getenv("MEETCUTE_AUTH", "").strip().lower() in ("on", "1", "true", "yes")

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
