"""MANUAL.md를 단일 소스로 사용해 /manual에 렌더링."""
from pathlib import Path

import markdown as md
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..config import BASE_DIR
from ..templating import templates

router = APIRouter(tags=["manual"])

MANUAL_PATH = BASE_DIR / "MANUAL.md"


@router.get("/manual", response_class=HTMLResponse)
def manual(request: Request):
    if not MANUAL_PATH.exists():
        body = "<p>MANUAL.md 파일이 없습니다.</p>"
    else:
        body = md.markdown(
            MANUAL_PATH.read_text(encoding="utf-8"),
            extensions=["fenced_code", "tables", "toc"],
        )
    return templates.TemplateResponse(
        request, "manual.html", {"body": body}
    )
