"""기존 업로드 사진에 썸네일 일괄 생성 — `python -m app.backfill_thumbs`.

썸네일 기능 도입 이전에 업로드된 사진은 _thumb 파일이 없어서, serve_upload 가
매번 원본(1600px) 으로 폴백 → 카드 그리드가 느림. 한 번만 돌려두면 새 사진처럼
빨라짐.

이미 _thumb 가 있는 사진은 건너뜀 (idempotent).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

from .config import UPLOAD_DIR

_ALLOWED = {".jpg", ".jpeg", ".png", ".webp"}
_THUMB_DIM = 800
_JPEG_QUALITY = 85


def _save_kwargs(ext: str) -> dict:
    if ext in (".jpg", ".jpeg"):
        return {"quality": _JPEG_QUALITY, "optimize": True, "progressive": True}
    if ext == ".webp":
        return {"quality": _JPEG_QUALITY, "method": 6}
    if ext == ".png":
        return {"optimize": True}
    return {}


def backfill(root: Path) -> tuple[int, int, int]:
    """returns (generated, skipped_existing, failed)."""
    generated = skipped = failed = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in _ALLOWED:
            continue
        if p.stem.endswith("_thumb"):
            continue
        thumb_path = p.parent / f"{p.stem}_thumb{ext}"
        if thumb_path.exists():
            skipped += 1
            continue
        try:
            img = Image.open(p)
            img = ImageOps.exif_transpose(img)
            if ext in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((_THUMB_DIM, _THUMB_DIM), Image.LANCZOS)
            img.save(thumb_path, **_save_kwargs(ext))
            generated += 1
        except Exception as e:
            failed += 1
            print(f"  실패: {p}: {e}", file=sys.stderr)
    return generated, skipped, failed


def main() -> int:
    print(f"썸네일 백필 시작: {UPLOAD_DIR}")
    g, s, f = backfill(UPLOAD_DIR)
    print(f"생성: {g}개  /  건너뜀(이미 있음): {s}개  /  실패: {f}개")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
