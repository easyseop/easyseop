"""pytest 전역 설정.

전략:
  - 환경변수는 conftest import 시 한 번만 박음 (앱 모듈 import 전).
  - app.* 은 한 번만 import — SQLAlchemy 메타데이터 중복 등록 회피.
  - 매 테스트마다 DB 파일을 새로 떨궈서 격리 (drop_all + create_all + init_db).
  - 텔레그램은 stub.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# ─── conftest import 시점: 앱 모듈 import 전에 환경변수 박기 ─────────────────
_TEST_BASE = Path("/tmp/meetcute-tests")
_TEST_BASE.mkdir(exist_ok=True)
_TEST_DATA = _TEST_BASE / "data"
_TEST_UPLOADS = _TEST_BASE / "uploads"
_TEST_DATA.mkdir(exist_ok=True)
_TEST_UPLOADS.mkdir(exist_ok=True)
_TEST_DB = _TEST_DATA / "test.db"

os.environ["MEETCUTE_DB_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["MEETCUTE_AUTH"] = "on"
os.environ["MEETCUTE_SECRET"] = "test-secret-x" * 4
os.environ.pop("MEETCUTE_TELEGRAM_BOT_TOKEN", None)
os.environ.pop("MEETCUTE_PUBLIC", None)

# ─── 앱 import (한 번만) ────────────────────────────────────────────────────
# config 의 BASE_DIR 도 임시 폴더로 강제해서 .encryption_key/.secret 격리.
from app import config as _cfg  # noqa: E402

_cfg.BASE_DIR = _TEST_BASE
_cfg.DATA_DIR = _TEST_DATA
_cfg.UPLOAD_DIR = _TEST_UPLOADS

from app import crypto  # noqa: E402, F401  (키 파일 만들어짐)
from app import database  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app import notifications  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


@pytest.fixture
def app_env(monkeypatch):
    """매 테스트마다 빈 DB + uploads. 한 번 import 된 app 을 재사용."""
    # DB 깨끗하게: drop_all + create_all + init_db (bootstrap 마이그/책임자 등)
    SQLModel.metadata.drop_all(database.engine)
    database.init_db()

    # uploads 디렉토리 비우기
    for child in _TEST_UPLOADS.iterdir():
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            import shutil
            shutil.rmtree(child)

    # 텔레그램 stub
    monkeypatch.setattr(notifications, "send_telegram", lambda *a, **kw: None)
    monkeypatch.setattr(notifications, "telegram_enabled", lambda: False)

    client = TestClient(fastapi_app)
    yield {
        "client": client,
        "engine": database.engine,
        "base": _TEST_BASE,
        "upload_dir": _TEST_UPLOADS,
        "db_path": _TEST_DB,
    }
    client.close()


@pytest.fixture
def client(app_env):
    return app_env["client"]


@pytest.fixture
def session(app_env):
    from sqlmodel import Session
    with Session(app_env["engine"]) as s:
        yield s
