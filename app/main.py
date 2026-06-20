from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import PROJECT_ROOT, ensure_config_files
from app.database import init_db


def create_app() -> FastAPI:
    ensure_config_files()
    init_db()
    app = FastAPI(title="StarMind Local Agent", version="0.1.0")
    app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")
    app.include_router(router)
    return app


app = create_app()

