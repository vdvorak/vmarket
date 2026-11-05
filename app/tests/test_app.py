import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from vmarket import db, metrics
from vmarket.config import Settings
from vmarket.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(database_path=tmp_path / "t.db", metrics_port=0)))


def test_health_ok(tmp_path: Path) -> None:
    r = make_client(tmp_path).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_503_when_db_broken(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    def broken() -> sqlite3.Connection:
        raise sqlite3.OperationalError("boom")

    client.app.state.connect = broken  # type: ignore[attr-defined]
    assert client.get("/api/health").status_code == 503


def test_migrate_is_idempotent_and_wal(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "m.db")
    db.migrate(conn)
    db.migrate(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1


def test_requests_are_counted_by_route(tmp_path: Path) -> None:
    make_client(tmp_path).get("/api/health")
    sample = metrics.REQUESTS.labels("GET", "/api/health", "200")._value.get()
    assert sample >= 1


def test_settings_from_env() -> None:
    s = Settings.from_env({"DATABASE_PATH": "/x/y.db", "METRICS_PORT": "0"})
    assert s.database_path == Path("/x/y.db") and s.metrics_port == 0
