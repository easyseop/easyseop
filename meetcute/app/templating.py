from datetime import date, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import AUTH_ENABLED, PUBLIC_MODE


def _human_time(value) -> str:
    """datetime/date → '방금', '3분 전', '오늘', '3일 전', '2주 전' 같은 상대 시간."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        now = datetime.utcnow()
        delta = now - value
        seconds = delta.total_seconds()
        if seconds < 0:
            # 미래
            seconds = -seconds
            if seconds < 3600:
                return f"{int(seconds // 60) or 1}분 후"
            if seconds < 86400:
                return f"{int(seconds // 3600)}시간 후"
            days = int(seconds // 86400)
            if days < 7:
                return f"{days}일 후"
            return value.strftime("%Y-%m-%d")
        if seconds < 60:
            return "방금"
        if seconds < 3600:
            return f"{int(seconds // 60)}분 전"
        if seconds < 86400:
            return f"{int(seconds // 3600)}시간 전"
        days = int(seconds // 86400)
        if days < 7:
            return f"{days}일 전"
        if days < 30:
            return f"{days // 7}주 전"
        if days < 365:
            return f"{days // 30}달 전"
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        today = date.today()
        days = (today - value).days
        if days == 0:
            return "오늘"
        if days == 1:
            return "어제"
        if days == -1:
            return "내일"
        if 0 < days < 7:
            return f"{days}일 전"
        if 0 > days > -7:
            return f"{-days}일 후"
        if days >= 7 and days < 30:
            return f"{days // 7}주 전"
        return value.strftime("%Y-%m-%d")
    return str(value)


def _thumb_url(filename: str) -> str:
    """카드/리스트 view 용 썸네일 경로. '12/abc.jpg' → '12/abc_thumb.jpg'.
    파일이 없으면 serve_upload 가 자동으로 원본으로 폴백 (옛 사진 호환)."""
    if not filename:
        return filename
    from pathlib import PurePosixPath
    p = PurePosixPath(filename)
    if p.stem.endswith("_thumb"):
        return filename  # 이미 thumb
    return str(p.with_name(p.stem + "_thumb" + p.suffix))


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["AUTH_ENABLED"] = AUTH_ENABLED
templates.env.globals["PUBLIC_MODE"] = PUBLIC_MODE
templates.env.filters["human_time"] = _human_time
templates.env.filters["thumb_url"] = _thumb_url