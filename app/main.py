"""FastAPI application factory."""

from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, auth, pages
from app.api.deps import CsrfFailed, LoginRequired, guard
from app.api.templating import render
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker, ping
from app.migrate import upgrade_to_head
from app.scoping import Forbidden, NotFound

# Public assets only: the stylesheet, self-hosted fonts and HTMX (visual-design.md §5).
# This mount is the one thing served without sign-in besides /login and /health, because the
# login page needs it; tests pin which file types may live here. Never put data in it.
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_PATH = "/static"


def _login_required(request: Request, exc: LoginRequired) -> Response:
    target = "/login"
    query = []
    if request.method in ("GET", "HEAD"):
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        query.append(f"next={quote(path, safe='')}")
    if exc.expired:
        query.append("expired=1")
    if query:
        target += "?" + "&".join(query)
    if request.headers.get("hx-request"):
        return PlainTextResponse(
            "Sign in required", status_code=401, headers={"HX-Redirect": target}
        )
    if request.method in ("GET", "HEAD"):
        return RedirectResponse(target, status_code=303)
    return PlainTextResponse("Sign in required", status_code=401)


def _error_page(request: Request, status_code: int, title: str, text: str) -> Response:
    return render(request, "error.html", {"title": title, "text": text}, status_code=status_code)


def _not_found(request: Request, _exc: Exception) -> Response:
    # The same page for "does not exist" and "not yours" (screens.md §6).
    return _error_page(
        request, 404, "Page not found", "This page does not exist, or you do not have access to it."
    )


def _forbidden(request: Request, _exc: Exception) -> Response:
    return _error_page(
        request,
        403,
        "No access",
        "Your roles do not include this page. Ask an admin if you need it.",
    )


def _csrf_failed(request: Request, _exc: Exception) -> Response:
    return _error_page(
        request,
        403,
        "Form expired",
        "This form has expired or did not come from invoice-flow. Reload the page and try again.",
    )


def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 404:
        return _not_found(request, exc)
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_migrate:
        upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)

    # Every route goes through `guard`: sign-in (except PUBLIC_PATHS) and CSRF.
    # No interactive API docs: they would be unauthenticated routes.
    app = FastAPI(
        title="invoice-flow",
        dependencies=[Depends(guard)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessionmaker(engine)

    app.add_exception_handler(LoginRequired, _login_required)
    app.add_exception_handler(NotFound, _not_found)
    app.add_exception_handler(Forbidden, _forbidden)
    app.add_exception_handler(CsrfFailed, _csrf_failed)
    app.add_exception_handler(StarletteHTTPException, _http_error)

    @app.get("/health")
    def health() -> dict[str, str]:
        db_ok = ping(engine)
        return {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "down"}

    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(admin.router)
    app.mount(STATIC_PATH, StaticFiles(directory=STATIC_DIR, html=False), name="static")
    return app
