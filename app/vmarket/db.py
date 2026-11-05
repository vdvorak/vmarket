"""SQLite v režimu WAL. Schéma se mění očíslovanými soubory v migrations/, verzi drží PRAGMA user_version."""

import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Spustí migrace novější než PRAGMA user_version; každá v jedné transakci, chyba vrací změny zpět."""
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for file in sorted(MIGRATIONS.glob("*.sql")):
        number = int(file.name.split("_", 1)[0])
        if number <= version:
            continue
        try:
            conn.executescript(f"BEGIN;\n{file.read_text()}\nPRAGMA user_version = {number};")
        except Exception:
            conn.rollback()
            raise
        conn.commit()
