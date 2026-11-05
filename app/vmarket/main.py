"""Aplikace FastAPI: API pod /api. Frontend (SolidJS) přijde ve fázi 3."""

from fastapi import FastAPI

from vmarket import db, metrics
from vmarket.api import health
from vmarket.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    conn = db.connect(settings.database_path)
    db.migrate(conn)
    conn.close()
    app = FastAPI(title="vmarket", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.connect = lambda: db.connect(settings.database_path)
    app.include_router(health.router)
    app.add_middleware(metrics.Metrics)
    metrics.start_server(settings.metrics_port)
    return app
