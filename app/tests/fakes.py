"""Fake adaptéry s počítadlem volání a vložitelnými chybami; napodobují i import payloadu po restartu."""

import json
from collections import defaultdict

from vmarket.provisioning.ports import AdapterError, Adapters, MachineSpec, PermanentError, Secrets

TOKENS = ":root {\n\t--color-primary: #1f5fd6;\n\t--color-accent: #f4b200; /* komentář */\n}\n"


class World:
    """Společný stav fake služeb a řízení chyb: fail[(metoda)] = kolikrát ještě selže."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = defaultdict(int)
        self.fail: dict[str, int] = {}
        self.files: dict[str, bytes] = {"/app/design/tokens.css": TOKENS.encode()}
        self.machine_ready = False
        self.healthy = False
        self.logs: list[str] = []
        self.secrets: dict[str, str] = {}
        self.dns: dict[str, str] = {}
        self.spec: MachineSpec | None = None
        self.smtp_error = ""

    def hit(self, name: str) -> None:
        self.calls[name] += 1
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise AdapterError(f"{name} spadlo")


class FakeFly:
    def __init__(self, w: World) -> None:
        self.w = w

    def ensure_app(self, name: str) -> None:
        self.w.hit("ensure_app")

    def ensure_public_ips(self, app: str) -> None:
        self.w.hit("ensure_public_ips")

    def ensure_volume(self, app: str, name: str, size_gb: int, region: str) -> None:
        self.w.hit("ensure_volume")

    def set_secrets(self, app: str, secrets: dict[str, str]) -> None:
        self.w.hit("set_secrets")
        self.w.secrets.update(secrets)

    def ensure_machine(self, app: str, spec: MachineSpec, region: str) -> str:
        self.w.hit("ensure_machine")
        self.w.spec = spec
        self.w.machine_ready = True
        return "m1"

    def ensure_certificate(self, app: str, hostname: str) -> None:
        self.w.hit("ensure_certificate")

    def write_files(self, app: str, machine_id: str, files: dict[str, bytes], owner: str) -> None:
        self.w.hit("write_files")
        assert owner == "zaci"
        self.w.files.update(files)

    def read_file(self, app: str, machine_id: str, path: str) -> bytes | None:
        self.w.hit("read_file")
        return self.w.files.get(path)

    def restart(self, app: str, machine_id: str) -> None:
        """Aplikace při startu naimportuje payload (verze 1) nebo skončí s chybou v logu."""
        self.w.hit("restart")
        raw = self.w.files.get("/data/provision.json")
        if raw is None:
            self.w.healthy = True
            return
        try:
            assert json.loads(raw)["version"] == 1
        except (KeyError, AssertionError, ValueError):
            self.w.healthy = False
            self.w.logs.append("Provisioning Payload nejde naimportovat: version: musí být 1")
            return
        self.w.files["/data/provision.json.done"] = self.w.files.pop("/data/provision.json")
        self.w.healthy = True

    def stop_machine(self, app: str) -> None:
        self.w.hit("stop_machine")
        self.w.machine_ready = False

    def start_machine(self, app: str) -> None:
        self.w.hit("start_machine")
        self.w.machine_ready = True

    def destroy_app(self, app: str) -> None:
        self.w.hit("destroy_app")
        self.w.machine_ready = False

    def recent_logs(self, app: str) -> list[str]:
        return self.w.logs


class FakeDns:
    def __init__(self, w: World) -> None:
        self.w = w

    def ensure_cname(self, name: str, target: str) -> None:
        self.w.hit("ensure_cname")
        self.w.dns[name] = target

    def delete_cname(self, name: str) -> None:
        self.w.hit("delete_cname")
        self.w.dns.pop(name, None)


class FakeStore:
    def __init__(self, w: World) -> None:
        self.w = w

    def ensure_backup_bucket(self, name: str) -> Secrets:
        self.w.hit("ensure_backup_bucket")
        return Secrets({"LITESTREAM_REPLICA_URL": f"s3://{name}", "LITESTREAM_ACCESS_KEY_ID": "k"})


class FakeSmtp:
    def __init__(self, w: World) -> None:
        self.w = w
        self.checked: list[tuple[str, int, str, str]] = []

    def check(self, host: str, port: int, user: str, password: str) -> None:
        self.w.hit("smtp_check")
        if self.w.smtp_error:
            raise PermanentError(self.w.smtp_error)
        self.checked.append((host, port, user, password))


class FakeProbe:
    def __init__(self, w: World) -> None:
        self.w = w

    def get(self, url: str) -> int | None:
        self.w.hit("probe")
        return 200 if self.w.healthy else None


def make(w: World) -> Adapters:
    return Adapters(FakeFly(w), FakeDns(w), FakeStore(w), FakeSmtp(w), FakeProbe(w))
