"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker, ping

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)

    app = FastAPI(title="invoice-flow")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessionmaker(engine)

    @app.get("/health")
    def health() -> dict[str, str]:
        db_ok = ping(engine)
        return {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "down"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, "home.html", {})

    return app
