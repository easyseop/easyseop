"""백업 명령 — `python -m app.backup` 로 한 줄 백업.

다음 파일/폴더를 하나의 timestamped tar.gz 로 묶음:
  - data/                  (SQLite DB)
  - uploads/               (사진)
  - .encryption_key        (필드 암호화 키)
  - .secret                (세션 쿠키 서명 키)

기본 출력: BASE_DIR/backups/meetcute-YYYYMMDD-HHMMSS.tar.gz
환경변수 MEETCUTE_BACKUP_DIR 로 다른 경로 지정 가능 (예: ~/Dropbox/...).

cron 예:
  0 2 * * * cd ~/easyseop/meetcute && /usr/bin/python -m app.backup

오래된 백업 자동 정리: --keep N (기본 14개 유지).
"""
from __future__ import annotations

import argparse
import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from .config import BASE_DIR, DATA_DIR, UPLOAD_DIR


def _default_backup_dir() -> Path:
    env = os.getenv("MEETCUTE_BACKUP_DIR", "").strip()
    return Path(env).expanduser() if env else BASE_DIR / "backups"


def _candidates() -> list[tuple[Path, str]]:
    """(절대경로, tar 내 arcname) 쌍 — 존재하는 것만."""
    items = [
        (DATA_DIR, "data"),
        (UPLOAD_DIR, "uploads"),
        (BASE_DIR / ".encryption_key", ".encryption_key"),
        (BASE_DIR / ".secret", ".secret"),
    ]
    return [(p, n) for (p, n) in items if p.exists()]


def make_backup(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or _default_backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"meetcute-{ts}.tar.gz"
    items = _candidates()
    if not items:
        raise RuntimeError("백업할 게 없습니다 (data/uploads/키 파일 모두 미존재)")
    with tarfile.open(out_file, "w:gz") as tar:
        for src, arc in items:
            tar.add(src, arcname=arc)
    return out_file


def prune_old(out_dir: Path, keep: int) -> list[Path]:
    """가장 최신 keep 개만 남기고 나머지 삭제. 삭제된 파일 목록 반환."""
    if keep <= 0:
        return []
    files = sorted(
        out_dir.glob("meetcute-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_remove = files[keep:]
    for f in to_remove:
        f.unlink(missing_ok=True)
    return to_remove


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.backup")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="백업 출력 폴더 (기본: BASE_DIR/backups 또는 MEETCUTE_BACKUP_DIR)",
    )
    p.add_argument(
        "--keep",
        type=int,
        default=14,
        help="최신 N 개만 유지하고 나머지 삭제 (기본 14, 0 = 끄기)",
    )
    p.add_argument("--quiet", action="store_true", help="성공 시 출력 안 함")
    args = p.parse_args(argv)

    out_dir = args.out or _default_backup_dir()
    out_file = make_backup(out_dir)
    removed = prune_old(out_dir, args.keep)

    if not args.quiet:
        size = _human_size(out_file.stat().st_size)
        print(f"백업 완료: {out_file}  ({size})")
        if removed:
            print(f"오래된 백업 {len(removed)}개 삭제")
    return 0


if __name__ == "__main__":
    sys.exit(main())
