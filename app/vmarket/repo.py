"""Repository: zápisy do DB a stavový automat Instance. Každá změna stavu jde přes `transition` a zapíše se do event logu."""

import json
import sqlite3
from typing import Literal

from vmarket.manifest import Manifest

InstanceState = Literal["pending", "provisioning", "running", "suspended", "failed", "deleted"]

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"provisioning", "deleted"},
    "provisioning": {"running", "failed"},
    "running": {"suspended", "deleted"},
    "suspended": {"running", "deleted"},
    "failed": {"provisioning", "deleted"},
    "deleted": set(),
}


class TransitionError(ValueError):
    pass


def log_event(conn: sqlite3.Connection, kind: str, detail: str = "", instance_id: int | None = None) -> None:
    conn.execute("INSERT INTO event_log (kind, detail, instance_id) VALUES (?, ?, ?)", (kind, detail, instance_id))


def register_app(conn: sqlite3.Connection, manifest: Manifest) -> None:
    """Zaregistruje App, nebo aktualizuje existující (Manifest, Plány podle id). Plány se nemažou, jen se přepisují."""
    with conn:
        conn.execute(
            """INSERT INTO apps (id, name, description, image, manifest_json) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, description = excluded.description, image = excluded.image,
            manifest_json = excluded.manifest_json, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (manifest.id, manifest.name, manifest.description, manifest.image, json.dumps(manifest.raw)),
        )
        for p in manifest.plans:
            conn.execute(
                """INSERT INTO plans (app_id, plan_key, name, interval, price_czk) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(app_id, plan_key) DO UPDATE SET name = excluded.name, interval = excluded.interval,
                price_czk = excluded.price_czk""",
                (manifest.id, p.key, p.name, p.interval, p.price_czk),
            )
        log_event(conn, "app.registered", manifest.id)


def get_or_create_customer(conn: sqlite3.Connection, email: str) -> int:
    with conn:
        conn.execute("INSERT OR IGNORE INTO customers (email) VALUES (?)", (email,))
    row = conn.execute("SELECT id FROM customers WHERE email = ?", (email,)).fetchone()
    return int(row["id"])


def add_release(conn: sqlite3.Connection, *, app_id: str, tag: str, scan_attestation: dict[str, object] | None = None) -> int:
    """Zapíše přijatý Release; image_ref je image z App a tag. (Release API s Security Gate přijde ve fázi 4.)"""
    with conn:
        app = conn.execute("SELECT image, manifest_json FROM apps WHERE id = ?", (app_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO releases (app_id, tag, image_ref, manifest_json, scan_attestation_json) VALUES (?, ?, ?, ?, ?)",
            (app_id, tag, f"{app['image']}:{tag}", app["manifest_json"], json.dumps(scan_attestation) if scan_attestation else None),
        )
        log_event(conn, "release.accepted", f"{app_id}:{tag}")
    return int(cur.lastrowid or 0)


def create_comped_instance(
    conn: sqlite3.Connection, *, app_id: str, customer_id: int, slug: str, inputs: dict[str, object] | None = None
) -> int:
    """Inputs: id Inputu → hodnota; obrázky jako {"$base64": "..."}."""
    with conn:
        cur = conn.execute(
            "INSERT INTO instances (app_id, customer_id, slug, billing_mode, inputs_json) VALUES (?, ?, ?, 'comped', ?)",
            (app_id, customer_id, slug, json.dumps(inputs or {})),
        )
        instance_id = int(cur.lastrowid or 0)
        log_event(conn, "instance.created", "comped", instance_id)
    return instance_id


def check_transition(conn: sqlite3.Connection, instance_id: int, new_state: str) -> None:
    """Vyhodí TransitionError (nebo LookupError), když přechod není povolený; nic nemění.
    Comped Instance nejde suspendovat."""
    row = conn.execute("SELECT state, billing_mode FROM instances WHERE id = ?", (instance_id,)).fetchone()
    if row is None:
        raise LookupError(f"Instance {instance_id} neexistuje")
    if new_state not in TRANSITIONS[row["state"]]:
        raise TransitionError(f"{row['state']} → {new_state} není povolený přechod")
    if new_state == "suspended" and row["billing_mode"] == "comped":
        raise TransitionError("Comped Instance se nesuspenduje")


def transition(conn: sqlite3.Connection, instance_id: int, new_state: InstanceState, detail: str = "") -> None:
    """Přechod stavu podle TRANSITIONS; neplatný přechod vyhodí TransitionError a nic nezapíše.
    `failed` ukládá důvod, ostatní stavy ho nulují."""
    with conn:
        check_transition(conn, instance_id, new_state)
        old = conn.execute("SELECT state FROM instances WHERE id = ?", (instance_id,)).fetchone()["state"]
        conn.execute(
            """UPDATE instances SET state = ?, failure_reason = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            deleted_at = CASE WHEN ? = 'deleted' THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') ELSE deleted_at END WHERE id = ?""",
            (new_state, detail if new_state == "failed" else None, new_state, instance_id),
        )
        log_event(conn, f"instance.{new_state}", f"{old} → {new_state}" + (f": {detail}" if detail else ""), instance_id)
