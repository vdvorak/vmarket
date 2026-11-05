"""Suspend, obnovení a smazání Instance. Nejdřív se zkontroluje, že přechod je povolený (Comped se nesuspenduje),
pak se zavolá služba a teprve po úspěchu se změní stav. Volání služeb jsou idempotentní, takže po chybě stačí příkaz zopakovat."""

import sqlite3

from vmarket import repo
from vmarket.provisioning.engine import Config
from vmarket.provisioning.ports import Adapters


def _app_name(conn: sqlite3.Connection, instance_id: int) -> str:
    row = conn.execute("SELECT fly_app_name FROM instances WHERE id = ?", (instance_id,)).fetchone()
    if row is None or not row["fly_app_name"]:
        raise repo.TransitionError("Instance ještě nemá Fly app (neproběhl Provisioning)")
    return str(row["fly_app_name"])


def suspend(conn: sqlite3.Connection, instance_id: int, adapters: Adapters, reason: str = "") -> None:
    repo.check_transition(conn, instance_id, "suspended")
    adapters.fly.stop_machine(_app_name(conn, instance_id))
    repo.transition(conn, instance_id, "suspended", reason)


def resume(conn: sqlite3.Connection, instance_id: int, adapters: Adapters) -> None:
    repo.check_transition(conn, instance_id, "running")
    state = conn.execute("SELECT state FROM instances WHERE id = ?", (instance_id,)).fetchone()["state"]
    if state != "suspended":
        raise repo.TransitionError(f"{state} → running řeší Provisioning, ne resume")
    adapters.fly.start_machine(_app_name(conn, instance_id))
    repo.transition(conn, instance_id, "running")


def delete(conn: sqlite3.Connection, instance_id: int, adapters: Adapters, config: Config) -> None:
    """Zruší Fly app (stroj i volume) a DNS záznam. Záloha v R2 zůstává (poslední záloha se drží po zrušení, viz plán)."""
    repo.check_transition(conn, instance_id, "deleted")
    row = conn.execute("SELECT fly_app_name, slug FROM instances WHERE id = ?", (instance_id,)).fetchone()
    if row["fly_app_name"]:  # Instance, která nikdy neprošla Provisioningem, nemá co rušit
        adapters.dns.delete_cname(f"{row['slug']}.{config.base_domain}")
        adapters.fly.destroy_app(row["fly_app_name"])
    repo.transition(conn, instance_id, "deleted")
