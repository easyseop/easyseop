from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import AUTH_ENABLED, PUBLIC_MODE

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["AUTH_ENABLED"] = AUTH_ENABLED
templates.env.globals["PUBLIC_MODE"] = PUBLIC_MODE
