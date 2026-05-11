from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import AUTH_ENABLED

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["AUTH_ENABLED"] = AUTH_ENABLED
