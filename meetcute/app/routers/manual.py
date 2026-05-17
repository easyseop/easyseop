"""매뉴얼 — 인덱스 + 3개 섹션 (웹 / 봇 / 운영) 으로 분리."""
from pathlib import Path

import markdown as md
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..config import BASE_DIR
from ..templating import templates

router = APIRouter(tags=["manual"])

DOCS_DIR = BASE_DIR / "docs"

SECTIONS = {
    "web": {
        "title": "웹 사용법",
        "icon": "🌐",
        "summary": "매물 / 만남 / 매칭 / 호환성 — 평소 쓰는 거 다 여기",
        "file": DOCS_DIR / "MANUAL_WEB.md",
    },
    "bot": {
        "title": "텔레그램 봇",
        "icon": "🤖",
        "summary": "/start, /form, /done — 봇 명령어 + 알림 종류",
        "file": DOCS_DIR / "MANUAL_BOT.md",
    },
    "ops": {
        "title": "운영 가이드",
        "icon": "🔧",
        "summary": "서버 띄우기 / 외부 노출 / 백업 / 인증 / 보안 (책임자용)",
        "file": DOCS_DIR / "MANUAL_OPS.md",
    },
}

MD_EXT = ["fenced_code", "tables", "toc"]


@router.get("/manual", response_class=HTMLResponse)
def manual_index(request: Request):
    return templates.TemplateResponse(
        request, "manual_index.html", {"sections": SECTIONS}
    )


@router.get("/manual/{section}", response_class=HTMLResponse)
def manual_section(section: str, request: Request):
    if section not in SECTIONS:
        raise HTTPException(404, f"Unknown manual section: {section}")
    meta = SECTIONS[section]
    path: Path = meta["file"]
    if not path.exists():
        body = "<p>매뉴얼 파일이 없습니다.</p>"
    else:
        body = md.markdown(path.read_text(encoding="utf-8"), extensions=MD_EXT)
    return templates.TemplateResponse(
        request,
        "manual_section.html",
        {
            "section_key": section,
            "section_title": meta["title"],
            "section_icon": meta["icon"],
            "sections": SECTIONS,
            "body": body,
        },
    )
