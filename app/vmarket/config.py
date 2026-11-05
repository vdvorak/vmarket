"""Nastavení výhradně z proměnných prostředí (App Contract: Configuration)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    metrics_port: int  # 0 = vypnuto

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        return cls(
            database_path=Path(e.get("DATABASE_PATH", "/data/vmarket.db")),
            metrics_port=int(e.get("METRICS_PORT", "9091")),
        )
