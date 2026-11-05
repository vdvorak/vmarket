"""Fly Machines API (https://api.machines.dev, referenční spec: docs.fly.io/api/machines). Instance = jedna Fly app s jedním
strojem a jedním volume `data`. Soubory na stroj se zapisují přes `exec` (stdin → base64 -d), protože Machines API
nemá nahrávání souborů za běhu; `files` v konfiguraci stroje by se po restartu přepsaly.

Neověřeno naostro: `recent_logs` používá GraphQL/REST rozhraní, které flyctl používá pro `fly logs`
(GET api.fly.io/api/v1/apps/<app>/logs), není v oficiální Machines specifikaci. Při chybě vrací prázdný seznam,
provisioner pak jen nemá příčinu z logu."""

import base64
import shlex
from typing import Any

import httpx2

from vmarket.adapters.http import Api
from vmarket.provisioning.ports import AdapterError, MachineSpec, PermanentError

MACHINES_URL = "https://api.machines.dev"
LOGS_URL = "https://api.fly.io"


class FlyApi:
    def __init__(self, token: str, org_slug: str, *, transport: httpx2.BaseTransport | None = None) -> None:
        self.api = Api(MACHINES_URL, token, transport=transport)
        self.logs_api = Api(LOGS_URL, token, transport=transport)
        self.org = org_slug

    # --- app, volume, secrets, certifikát ---

    def ensure_app(self, name: str) -> None:
        if self.api.request("GET", f"/v1/apps/{name}", allow={404}).status_code == 404:
            r = self.api.request("POST", "/v1/apps", json={"name": name, "org_slug": self.org}, allow={409, 422})
            if r.status_code in {409, 422} and self.api.request("GET", f"/v1/apps/{name}", allow={404}).status_code == 404:
                raise PermanentError(f"Fly app {name} nejde založit: {r.text[:300]}")

    def ensure_volume(self, app: str, name: str, size_gb: int, region: str) -> None:
        volumes = self.api.json("GET", f"/v1/apps/{app}/volumes")
        if any(v["name"] == name and v.get("state") != "destroyed" for v in volumes):
            return
        self.api.json("POST", f"/v1/apps/{app}/volumes", json={"name": name, "size_gb": size_gb, "region": region, "snapshot_retention": 5})

    def set_secrets(self, app: str, secrets: dict[str, str]) -> None:
        # Běžící stroje secrets samy nevidí; provisioner je nastavuje před vytvořením stroje.
        self.api.json("POST", f"/v1/apps/{app}/secrets", json={"values": secrets})

    def ensure_certificate(self, app: str, hostname: str) -> None:
        if self.api.request("GET", f"/v1/apps/{app}/certificates/{hostname}", allow={404}).status_code == 404:
            self.api.json("POST", f"/v1/apps/{app}/certificates/acme", json={"hostname": hostname})

    # --- stroj ---

    def _machines(self, app: str) -> list[dict[str, Any]]:
        machines: list[dict[str, Any]] = self.api.json("GET", f"/v1/apps/{app}/machines")
        return [m for m in machines if m.get("state") != "destroyed"]

    def _machine_id(self, app: str) -> str:
        machines = self._machines(app)
        if not machines:
            raise AdapterError(f"Fly app {app} nemá žádný stroj")
        return str(machines[0]["id"])

    def ensure_machine(self, app: str, spec: MachineSpec, region: str) -> str:
        if machines := self._machines(app):
            return str(machines[0]["id"])
        config: dict[str, Any] = {
            "image": spec.image,
            "env": spec.env,
            "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": spec.memory_mb},
            "mounts": [{"volume": spec.volume, "path": spec.mount}],
            "restart": {"policy": "on-failure", "max_retries": 5},
            "services": [
                {
                    "protocol": "tcp",
                    "internal_port": spec.port,
                    "ports": [{"port": 80, "handlers": ["http"], "force_https": True}, {"port": 443, "handlers": ["tls", "http"]}],
                    # Instance, za kterou se platí, neusíná; šetření (min_machines_running 0) je věc Planu, až bude.
                    "autostop": "off",
                    "autostart": True,
                    "min_machines_running": 1,
                    "checks": [
                        {
                            "type": "http",
                            "method": "GET",
                            "path": spec.health_path,
                            "interval": "30s",
                            "timeout": "5s",
                            "grace_period": f"{spec.health_grace_s}s",
                        }
                    ],
                }
            ],
        }
        if spec.metrics_port:
            config["metrics"] = {"port": spec.metrics_port, "path": spec.metrics_path}
        m = self.api.json("POST", f"/v1/apps/{app}/machines", json={"region": region, "config": config})
        mid = str(m["id"])
        self._wait(app, mid, "started")
        return mid

    def _wait(self, app: str, machine_id: str, state: str) -> None:
        r = self.api.request("GET", f"/v1/apps/{app}/machines/{machine_id}/wait", params={"state": state, "timeout": 60}, allow={408})
        if r.status_code == 408:
            raise AdapterError(f"Stroj {machine_id} se do 60 s nedostal do stavu {state}")

    def restart(self, app: str, machine_id: str) -> None:
        self.api.json("POST", f"/v1/apps/{app}/machines/{machine_id}/restart")
        self._wait(app, machine_id, "started")

    def stop_machine(self, app: str) -> None:
        for m in self._machines(app):
            if m["state"] not in {"stopped", "stopping"}:
                self.api.json("POST", f"/v1/apps/{app}/machines/{m['id']}/stop")

    def start_machine(self, app: str) -> None:
        mid = self._machine_id(app)
        self.api.json("POST", f"/v1/apps/{app}/machines/{mid}/start", allow={412})
        self._wait(app, mid, "started")

    def destroy_app(self, app: str) -> None:
        self.api.request("DELETE", f"/v1/apps/{app}", allow={404})

    # --- soubory a logy ---

    def _exec(self, app: str, machine_id: str, command: list[str], stdin: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"command": command, "timeout": 30}
        if stdin is not None:
            body["stdin"] = stdin
        result: dict[str, Any] = self.api.json("POST", f"/v1/apps/{app}/machines/{machine_id}/exec", json=body)
        return result

    def write_files(self, app: str, machine_id: str, files: dict[str, bytes], owner: str) -> None:
        # Slovník drží pořadí; provisioner dává payload poslední (jeho přítomnost spouští import).
        for path, data in files.items():
            directory = path.rsplit("/", 1)[0]
            script = (
                f"mkdir -p {shlex.quote(directory)} && base64 -d > {shlex.quote(path)} && chown {shlex.quote(owner)} {shlex.quote(path)}"
            )
            res = self._exec(app, machine_id, ["sh", "-c", script], stdin=base64.b64encode(data).decode())
            if res.get("exit_code") != 0:
                raise AdapterError(f"Zápis {path} selhal: {res.get('stderr', '')[:200]}")
        directories = {p.rsplit("/", 1)[0] for p in files}
        for directory in directories:
            # Adresář brandingu založil root; aplikace do něj nepíše, ale musí z něj číst a /data musí zůstat jejím.
            self._exec(app, machine_id, ["chown", owner, directory])

    def read_file(self, app: str, machine_id: str, path: str) -> bytes | None:
        res = self._exec(app, machine_id, ["sh", "-c", f"test -f {shlex.quote(path)} && base64 {shlex.quote(path)}"])
        if res.get("exit_code") != 0:
            return None
        return base64.b64decode(res.get("stdout", ""))

    def recent_logs(self, app: str) -> list[str]:
        try:
            data = self.logs_api.json("GET", f"/api/v1/apps/{app}/logs")
            return [str(e["attributes"]["message"]) for e in data["data"]]
        except (AdapterError, Exception):  # noqa: BLE001 - neověřené rozhraní, logy jsou jen pomůcka k diagnóze
            return []
