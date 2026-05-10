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

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
