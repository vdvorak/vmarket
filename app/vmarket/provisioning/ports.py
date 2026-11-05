"""Rozhraní k vnějším službám. Provisioner zná jen tyto protokoly; skutečné adaptéry (Fly Machines API, Cloudflare, R2,
SMTP) přijdou v issue "Ověření fáze 2", v testech jsou fake. Každá metoda `ensure_*` musí být idempotentní:
druhé volání se stejnými argumenty nic nezmění a vrátí totéž. Chyba služby = `AdapterError` (provisioner zkusí znovu)."""

from dataclasses import dataclass, field
from typing import Protocol


class AdapterError(Exception):
    """Dočasná nebo neznámá chyba vnější služby; krok se opakuje s odstupem."""


class PermanentError(Exception):
    """Chyba, kterou opakování nspraví jen málokdy (špatné Inputs, nevalidní payload). Krok se neopakuje."""


@dataclass(frozen=True)
class Secrets:
    """Přístupy, které krok vyrobí a další kroky použijí (jdou do Fly secrets)."""

    values: dict[str, str]


@dataclass(frozen=True)
class MachineSpec:
    image: str
    env: dict[str, str]
    memory_mb: int
    port: int
    volume: str
    mount: str
    health_path: str
    health_grace_s: int
    metrics_port: int | None = None
    metrics_path: str = "/metrics"
    files: dict[str, bytes] = field(default_factory=dict)


class Fly(Protocol):
    def ensure_app(self, name: str) -> None: ...
    def ensure_public_ips(self, app: str) -> None:
        """Přidělí app veřejné IP (sdílená IPv4 a IPv6, zdarma). Machines API je při založení app nepřidělí a bez nich se <app>.fly.dev nepřeloží."""
        ...

    def ensure_volume(self, app: str, name: str, size_gb: int, region: str) -> None: ...
    def set_secrets(self, app: str, secrets: dict[str, str]) -> None: ...
    def ensure_machine(self, app: str, spec: MachineSpec, region: str) -> str:
        """Vrátí id stroje; stroj už existuje → vrátí jeho id beze změny."""
        ...

    def ensure_certificate(self, app: str, hostname: str) -> None: ...
    def write_files(self, app: str, machine_id: str, files: dict[str, bytes], owner: str) -> None:
        """Zapíše soubory na stroj (adresáře založí) a nastaví vlastníka; stroj musí běžet."""
        ...

    def read_file(self, app: str, machine_id: str, path: str) -> bytes | None: ...
    def restart(self, app: str, machine_id: str) -> None: ...
    def stop_machine(self, app: str) -> None:
        """Zastaví stroj Instance (Suspend); data na volume zůstanou. Už zastavený stroj není chyba."""
        ...

    def start_machine(self, app: str) -> None: ...
    def destroy_app(self, app: str) -> None:
        """Zruší Fly app včetně stroje a volume. Neexistující app není chyba."""
        ...

    def recent_logs(self, app: str) -> list[str]: ...


class Dns(Protocol):
    def ensure_cname(self, name: str, target: str) -> None: ...
    def delete_cname(self, name: str) -> None: ...


class ObjectStore(Protocol):
    def ensure_backup_bucket(self, name: str) -> Secrets:
        """Vrátí LITESTREAM_* proměnné pro bucket."""
        ...


class Smtp(Protocol):
    def check(self, host: str, port: int, user: str, password: str) -> None:
        """Ověří spojení, TLS a přihlášení. Špatné přihlašovací údaje nebo nedostupný server s jasnou chybou = PermanentError,
        dočasná síťová chyba = AdapterError. E-mail se neodesílá."""
        ...


class Probe(Protocol):
    def get(self, url: str) -> int | None:
        """HTTP stav, nebo None, když se nepodařilo spojit."""
        ...


@dataclass(frozen=True)
class Adapters:
    fly: Fly
    dns: Dns
    store: ObjectStore
    smtp: Smtp
    probe: Probe
