"""Admin CLI (MVP: jediné ovládání vmarketu). `python -m vmarket.cli <příkaz>`; databáze z DATABASE_PATH.

  app register <vmarket.yaml>             zaregistruje nebo aktualizuje App z Manifestu
  release add <app> <tag>                 zapíše přijatý Release (image = image z App + tag)
  instance comp --app A --slug S --email E [--inputs inputs.json] [--domain zaci.example.cz] [--no-provision]
                                          založí Comped Instance a provede Provisioning
  instance provision <id|slug>            dokončí/zopakuje Provisioning po `failed`
  instance suspend|resume|delete <id|slug>
  status [<id|slug>]                      přehled Instancí, nebo detail s posledními událostmi

Soubor s Inputy je JSON (id Inputu → hodnota); obrázek se zadá jako {"$file": "cesta/relativní/k/souboru"}."""

import argparse
import base64
import json
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from vmarket import db, lifecycle, manifest, repo
from vmarket.adapters import factory
from vmarket.provisioning.engine import Config, provision
from vmarket.provisioning.ports import Adapters


class CliError(Exception):
    pass


def default_adapters() -> Adapters:
    try:
        return factory.from_env()
    except factory.ConfigError as e:
        raise CliError(str(e)) from e


def _load_inputs(path: Path) -> dict[str, Any]:
    def resolve(value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"$file"}:
            return {"$base64": base64.b64encode((path.parent / value["$file"]).read_bytes()).decode()}
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CliError(f"Inputy z {path} nejdou přečíst: {e}") from e
    if not isinstance(data, dict):
        raise CliError("Soubor s Inputy musí být JSON objekt")
    return dict(resolve(data))


def _instance_id(conn: sqlite3.Connection, ref: str) -> int:
    row = conn.execute("SELECT id FROM instances WHERE id = ? OR slug = ?", (ref if ref.isdigit() else -1, ref)).fetchone()
    if row is None:
        raise CliError(f"Instance {ref!r} neexistuje")
    return int(row["id"])


def _status(conn: sqlite3.Connection, ref: str | None, out: Callable[[str], None]) -> None:
    if ref is None:
        rows = conn.execute(
            """SELECT i.id, i.app_id, i.slug, i.state, i.billing_mode, i.updated_at FROM instances i ORDER BY i.id"""
        ).fetchall()
        if not rows:
            out("Žádné Instance.")
        for r in rows:
            out(f"{r['id']:>3}  {r['slug']:<20} {r['app_id']:<12} {r['state']:<13} {r['billing_mode']:<7} {r['updated_at']}")
        return
    row = conn.execute("SELECT * FROM instances WHERE id = ?", (_instance_id(conn, ref),)).fetchone()
    out(f"Instance {row['id']} ({row['slug']}): {row['state']}, {row['billing_mode']}, app {row['app_id']}")
    out(f"Fly app: {row['fly_app_name'] or '-'}")
    if row["custom_domain"]:
        target = f"{row['fly_app_name']}.fly.dev" if row["fly_app_name"] else "<fly app>.fly.dev"
        out(f"Vlastní doména: {row['custom_domain']}")
        out(f"  U zákazníka (DNS) nastavte: CNAME {row['custom_domain']} → {target}")
    if row["failure_reason"]:
        out(f"Selhání: {row['failure_reason']}")
    for s in conn.execute("SELECT * FROM provisioning_steps WHERE instance_id = ? ORDER BY rowid", (row["id"],)):
        err = f"  ({s['last_error']})" if s["last_error"] else ""
        out(f"  krok {s['step']:<14} {s['status']:<8} pokusů {s['attempts']}{err}")
    out("Poslední události:")
    for e in reversed(
        conn.execute("SELECT at, kind, detail FROM event_log WHERE instance_id = ? ORDER BY id DESC LIMIT 10", (row["id"],)).fetchall()
    ):
        out(f"  {e['at']}  {e['kind']}  {e['detail']}")


def run(
    argv: Sequence[str],
    conn: sqlite3.Connection,
    adapters_factory: Callable[[], Adapters] = default_adapters,
    config: Config | None = None,
    out: Callable[[str], None] = print,
) -> int:
    p = argparse.ArgumentParser(prog="vmarket")
    sub = p.add_subparsers(dest="cmd", required=True)
    app = sub.add_parser("app").add_subparsers(dest="sub", required=True)
    app.add_parser("register").add_argument("manifest", type=Path)
    rel = sub.add_parser("release").add_subparsers(dest="sub", required=True)
    add = rel.add_parser("add")
    add.add_argument("app")
    add.add_argument("tag")
    inst = sub.add_parser("instance").add_subparsers(dest="sub", required=True)
    comp = inst.add_parser("comp")
    comp.add_argument("--app", required=True)
    comp.add_argument("--slug", required=True)
    comp.add_argument("--email", required=True)
    comp.add_argument("--inputs", type=Path)
    comp.add_argument("--domain", help="vlastní doména zákazníka, např. zaci.ubouraka.cz")
    comp.add_argument("--no-provision", action="store_true")
    for name in ("provision", "suspend", "resume", "delete"):
        inst.add_parser(name).add_argument("ref")
    sub.add_parser("status").add_argument("ref", nargs="?")
    a = p.parse_args(argv)
    config = config or Config(base_domain=os.environ.get("VMARKET_BASE_DOMAIN", "apps.vitekdvorak.cz"))

    try:
        match (a.cmd, getattr(a, "sub", None)):
            case ("app", "register"):
                m = manifest.parse(a.manifest.read_text(encoding="utf-8"))
                repo.register_app(conn, m)
                out(f"App {m.id} zaregistrována ({len(m.plans)} Plánů).")
            case ("release", "add"):
                repo.add_release(conn, app_id=a.app, tag=a.tag)
                out(f"Release {a.app}:{a.tag} přijat.")
            case ("instance", "comp"):
                inputs = _load_inputs(a.inputs) if a.inputs else {}
                customer = repo.get_or_create_customer(conn, a.email)
                iid = repo.create_comped_instance(
                    conn, app_id=a.app, customer_id=customer, slug=a.slug, inputs=inputs, custom_domain=a.domain
                )
                out(f"Comped Instance {iid} ({a.slug}) založena.")
                if not a.no_provision:
                    out(f"Provisioning: {provision(conn, iid, adapters_factory(), config)}")
                    _status(conn, str(iid), out)
            case ("instance", "provision"):
                iid = _instance_id(conn, a.ref)
                state = provision(conn, iid, adapters_factory(), config)
                out(f"Provisioning: {state}")
                _status(conn, str(iid), out)
            case ("instance", "suspend"):
                lifecycle.suspend(conn, _instance_id(conn, a.ref), adapters_factory())
                out("Suspendováno.")
            case ("instance", "resume"):
                lifecycle.resume(conn, _instance_id(conn, a.ref), adapters_factory())
                out("Obnoveno.")
            case ("instance", "delete"):
                lifecycle.delete(conn, _instance_id(conn, a.ref), adapters_factory(), config)
                out("Smazáno.")
            case ("status", _):
                _status(conn, a.ref, out)
    except (CliError, manifest.ManifestError, repo.TransitionError, LookupError, sqlite3.IntegrityError, ValueError, OSError) as e:
        out(f"Chyba: {e}")
        return 1
    return 0


def main() -> None:
    conn = db.connect(Path(os.environ.get("DATABASE_PATH", "/data/vmarket.db")))
    db.migrate(conn)
    sys.exit(run(sys.argv[1:], conn))


if __name__ == "__main__":
    main()
