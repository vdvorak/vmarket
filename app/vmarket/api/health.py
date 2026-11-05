"""Liveness + readiness: aplikace běží a databáze odpovídá."""

import logging
import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/health")
log = logging.getLogger(__name__)


@router.get("")
def health(request: Request) -> JSONResponse:
    connect: Callable[[], sqlite3.Connection] = request.app.state.connect
    try:
        conn = connect()
        try:
            conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("Databáze neodpovídá.")
        return JSONResponse({"status": "error"}, status_code=503)
    return JSONResponse({"status": "ok"})
