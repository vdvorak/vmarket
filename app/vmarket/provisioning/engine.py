"""Provisioner: idempotentní stavový automat, který z Instance ve stavu `pending` (nebo `failed`) udělá `running`.

Postup je řada kroků; každý je idempotentní a jeho dokončení se zapíše do `provisioning_steps`, takže pád procesu
nebo opakování po `failed` pokračuje od prvního nehotového kroku. Pořadí vychází z ručního ověření (poznatky v issue #1):
první start stroje proběhne bez payloadu, soubory se nahrají až na běžící stroj a pak je nutný restart.

Chyba služby (`AdapterError`) se opakuje s odstupem, `PermanentError` (špatné Inputs, nevalidní payload) hned
ukončí Provisioning ve stavu `failed` s příčinou. Každý krok a pokus je v event logu."""

import base64
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vmarket import repo
from vmarket.manifest import Manifest, from_dict
from vmarket.provisioning import payload as payload_builder
from vmarket.provisioning.ports import AdapterError, Adapters, MachineSpec, PermanentError

PAYLOAD_ERROR_MARKER = "Provisioning Payload"


@dataclass(frozen=True)
class Config:
    base_domain: str  # Instance je na <slug>.<base_domain>
    region: str = "fra"
    app_prefix: str = "vm-"
    retries: int = 4
    backoff_s: float = 2.0
    health_attempts: int = 30
    health_interval_s: float = 2.0
    sleep: Callable[[float], None] = field(default=time.sleep, compare=False)


@dataclass
class Ctx:
    conn: sqlite3.Connection
    instance_id: int
    manifest: Manifest
    image: str
    slug: str
    fly_app: str
    inputs: dict[str, Any]
    adapters: Adapters
    config: Config
    out: dict[str, dict[str, Any]]

    @property
    def host(self) -> str:
        return f"{self.slug}.{self.config.base_domain}"

    @property
    def machine_id(self) -> str:
        return str(self.out["machine"]["id"])

    def secrets(self) -> dict[str, str]:
        return {**self.out["r2_bucket"]["secrets"], **self.out["mail_identity"]["secrets"]}


def _r2_bucket(c: Ctx) -> dict[str, Any]:
    return {"secrets": c.adapters.store.ensure_backup_bucket(c.fly_app).values}


def _mail_identity(c: Ctx) -> dict[str, Any]:
    return {"secrets": c.adapters.mail.ensure_sender(c.slug).values}


def _fly_app(c: Ctx) -> dict[str, Any]:
    c.adapters.fly.ensure_app(c.fly_app)
    return {}


def _fly_volume(c: Ctx) -> dict[str, Any]:
    res = c.manifest.raw["resources"]
    c.adapters.fly.ensure_volume(c.fly_app, "data", res["volume_gb"], c.config.region)
    return {}


def _fly_secrets(c: Ctx) -> dict[str, Any]:
    c.adapters.fly.set_secrets(c.fly_app, c.secrets())
    return {}


def _dns(c: Ctx) -> dict[str, Any]:
    c.adapters.dns.ensure_cname(c.host, f"{c.fly_app}.fly.dev")
    c.adapters.fly.ensure_certificate(c.fly_app, c.host)
    return {}


def _machine(c: Ctx) -> dict[str, Any]:
    res = c.manifest.raw["resources"]
    metrics = res.get("metrics") or {}
    env = {
        **(c.manifest.raw.get("env") or {}),
        "APP_URL": f"https://{c.host}",
        "LITESTREAM_REPLICA_URL": c.out["r2_bucket"]["secrets"].get("LITESTREAM_REPLICA_URL", ""),
    }
    spec = MachineSpec(
        image=c.image,
        env={k: v for k, v in env.items() if v},
        memory_mb=res["memory_mb"],
        port=res["port"],
        volume="data",
        mount=res["volume_mount"],
        health_path=res["health"]["path"],
        health_grace_s=res["health"]["grace_period_s"],
        metrics_port=metrics.get("port"),
        metrics_path=metrics.get("path", "/metrics"),
    )
    return {"id": c.adapters.fly.ensure_machine(c.fly_app, spec, c.config.region)}


def _upload(c: Ctx) -> dict[str, Any]:
    prov = c.manifest.raw["provisioning"]
    defaults_dir = prov.get("branding_defaults_dir")
    tokens = ""
    if any(i["type"] == "color_tokens" for i in c.manifest.raw["inputs"]):
        if not defaults_dir:
            raise PermanentError("Manifest má color_tokens, ale chybí provisioning.branding_defaults_dir")
        raw = c.adapters.fly.read_file(c.fly_app, c.machine_id, f"{defaults_dir}/tokens.css")
        if raw is None:
            raise AdapterError(f"Výchozí tokens.css nenalezen v {defaults_dir}")
        tokens = raw.decode()
    payload, branding = payload_builder.build(c.manifest, c.inputs, tokens)
    files = {f"{prov['branding_dir']}/{name}": data for name, data in branding.items()}
    # Payload jde na disk jako poslední: jeho existence spouští import po restartu.
    files[prov["payload_file"]] = json.dumps(payload, ensure_ascii=False).encode()
    c.adapters.fly.write_files(c.fly_app, c.machine_id, files, owner="zaci")
    return {}


def _restart(c: Ctx) -> dict[str, Any]:
    c.adapters.fly.restart(c.fly_app, c.machine_id)
    return {}


def _health(c: Ctx) -> dict[str, Any]:
    url = f"https://{c.fly_app}.fly.dev{c.manifest.raw['resources']['health']['path']}"
    for _ in range(c.config.health_attempts):
        if c.adapters.probe.get(url) == 200:
            return {}
        c.config.sleep(c.config.health_interval_s)
    logs = c.adapters.fly.recent_logs(c.fly_app)
    for line in reversed(logs):
        if PAYLOAD_ERROR_MARKER in line:
            raise PermanentError(f"Aplikace odmítla payload: {line.strip()}")
    raise AdapterError("Health check neprošel: " + " | ".join(logs[-5:]))


def _verify_import(c: Ctx) -> dict[str, Any]:
    done = c.manifest.raw["provisioning"]["payload_file"] + ".done"
    if c.adapters.fly.read_file(c.fly_app, c.machine_id, done) is None:
        raise PermanentError(f"Po startu chybí {done}: payload se neimportoval")
    return {}


# (název, funkce, počet pokusů; None = z Config). Health si čeká sám, proto jen jeden pokus navíc.
STEPS: list[tuple[str, Callable[[Ctx], dict[str, Any]], int | None]] = [
    ("r2_bucket", _r2_bucket, None),
    ("mail_identity", _mail_identity, None),
    ("fly_app", _fly_app, None),
    ("fly_volume", _fly_volume, None),
    ("fly_secrets", _fly_secrets, None),
    ("dns", _dns, None),
    ("machine", _machine, None),
    ("upload", _upload, None),
    ("restart", _restart, None),
    ("health", _health, 2),
    ("verify_import", _verify_import, 1),
]


def _load_ctx(conn: sqlite3.Connection, instance_id: int, adapters: Adapters, config: Config) -> Ctx:
    row = conn.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)).fetchone()
    app = conn.execute("SELECT * FROM apps WHERE id = ?", (row["app_id"],)).fetchone()
    release = (
        conn.execute("SELECT image_ref FROM releases WHERE id = ?", (row["release_id"],)).fetchone()
        if row["release_id"]
        else conn.execute(
            "SELECT image_ref FROM releases WHERE app_id = ? AND status IN ('accepted', 'canary', 'rolled_out') ORDER BY id DESC LIMIT 1",
            (row["app_id"],),
        ).fetchone()
    )
    if release is None:
        raise PermanentError(f"App {row['app_id']} nemá žádný přijatý Release, nemá co nasadit")
    fly_app = row["fly_app_name"] or f"{config.app_prefix}{row['app_id']}-{row['slug']}"
    with conn:
        conn.execute(
            "UPDATE instances SET fly_app_name = ?, release_id = coalesce(release_id, "
            "(SELECT id FROM releases WHERE image_ref = ? AND app_id = ?)) WHERE id = ?",
            (fly_app, release["image_ref"], row["app_id"], instance_id),
        )
    out = {
        r["step"]: json.loads(r["output_json"])
        for r in conn.execute("SELECT step, output_json FROM provisioning_steps WHERE instance_id = ? AND status = 'done'", (instance_id,))
    }
    return Ctx(
        conn,
        instance_id,
        from_dict(json.loads(app["manifest_json"])),
        release["image_ref"],
        row["slug"],
        fly_app,
        _decode(json.loads(row["inputs_json"])),
        adapters,
        config,
        out,
    )


def _decode(value: Any) -> Any:
    """{"$base64": "..."} (obrázek v uložených Inputech) → bytes."""
    if isinstance(value, dict):
        if set(value) == {"$base64"}:
            return base64.b64decode(value["$base64"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _record(
    conn: sqlite3.Connection, instance_id: int, step: str, status: str, *, attempts: int, error: str | None, out: dict[str, Any]
) -> None:
    with conn:
        conn.execute(
            """INSERT INTO provisioning_steps (instance_id, step, status, attempts, last_error, output_json, done_at)
            VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 'done' THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') END)
            ON CONFLICT(instance_id, step) DO UPDATE SET status = excluded.status, attempts = provisioning_steps.attempts + excluded.attempts,
            last_error = excluded.last_error, output_json = excluded.output_json, done_at = excluded.done_at""",
            (instance_id, step, status, attempts, error, json.dumps(out), status),
        )


def _run_step(c: Ctx, name: str, fn: Callable[[Ctx], dict[str, Any]], tries: int) -> None:
    last = ""
    for attempt in range(1, tries + 1):
        try:
            out = fn(c)
        except AdapterError as e:
            last = str(e)
            repo.log_event(c.conn, "provision.retry", f"{name} (pokus {attempt}/{tries}): {last}", c.instance_id)
            _record(c.conn, c.instance_id, name, "pending", attempts=1, error=last, out={})
            if attempt < tries:
                c.config.sleep(c.config.backoff_s * 2 ** (attempt - 1))
            continue
        c.out[name] = out
        _record(c.conn, c.instance_id, name, "done", attempts=1, error=None, out=out)
        repo.log_event(c.conn, "provision.step", name, c.instance_id)
        return
    raise PermanentError(f"Krok {name} selhal po {tries} pokusech: {last}")


def provision(conn: sqlite3.Connection, instance_id: int, adapters: Adapters, config: Config) -> str:
    """Provede (nebo dokončí) Provisioning a vrátí výsledný stav Instance (`running` nebo `failed`).
    Neplatný výchozí stav (např. `running`) vyhodí TransitionError."""
    repo.transition(conn, instance_id, "provisioning")
    current = ""
    try:
        ctx = _load_ctx(conn, instance_id, adapters, config)
        done = set(ctx.out)
        for name, fn, tries in STEPS:
            current = name
            if name in done:
                continue
            _run_step(ctx, name, fn, tries or config.retries)
    except PermanentError as e:
        _record(conn, instance_id, current or "setup", "failed", attempts=0, error=str(e), out={})
        repo.transition(conn, instance_id, "failed", str(e))
        return "failed"
    except Exception as e:
        repo.transition(conn, instance_id, "failed", f"Interní chyba: {e!r}")
        raise
    repo.transition(conn, instance_id, "running")
    return "running"
