import base64
import hashlib
import json
import smtplib
import ssl
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from vmarket.adapters import factory
from vmarket.adapters.cloudflare import CloudflareDns, R2Store
from vmarket.adapters.fly import FlyApi
from vmarket.adapters.probe import HttpProbe
from vmarket.adapters.smtp import SmtpChecker
from vmarket.provisioning.ports import AdapterError, MachineSpec, PermanentError

Handler = Callable[[httpx2.Request], httpx2.Response]


class Mock:
    """Zaznamenává požadavky a odpovídá podle (metoda, cesta); neznámá cesta je chyba testu."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        key = (request.method, request.url.path)
        self.calls.append((*key, body))
        route = self.routes[key]
        if callable(route):
            return route(request)  # type: ignore[no-any-return]
        status, payload = route if isinstance(route, tuple) else (200, route)
        return httpx2.Response(status, json=payload)

    def paths(self) -> list[str]:
        return [f"{m} {p}" for m, p, _ in self.calls]


SPEC = MachineSpec(
    image="ghcr.io/x/app:1.0.0", env={"A": "1"}, memory_mb=512, port=8000, volume="data", mount="/data",
    health_path="/api/health", health_grace_s=60, metrics_port=9091,
)  # fmt: skip


# --- Fly ---


def test_fly_ensure_app_creates_only_when_missing() -> None:
    m = Mock({("GET", "/v1/apps/a"): (404, {}), ("POST", "/v1/apps"): (201, {"id": "x"})})
    FlyApi("t", "org", transport=m.transport).ensure_app("a")
    assert m.calls[1][2] == {"name": "a", "org_slug": "org"}  # app_name vedle name Fly odmítá (400)
    m2 = Mock({("GET", "/v1/apps/a"): {"name": "a"}})
    FlyApi("t", "org", transport=m2.transport).ensure_app("a")
    assert m2.paths() == ["GET /v1/apps/a"]


def test_fly_app_creation_refused_is_permanent_not_retried() -> None:
    m = Mock({("GET", "/v1/apps/a"): (404, {}), ("POST", "/v1/apps"): (422, {"error": "We need your payment information to continue!"})})
    with pytest.raises(PermanentError, match="payment information"):
        FlyApi("t", "o", transport=m.transport).ensure_app("a")


def test_fly_sends_bearer_token() -> None:
    seen: list[str] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        seen.append(r.headers["authorization"])
        return httpx2.Response(200, json={})

    FlyApi("tok", "org", transport=Mock({("GET", "/v1/apps/a"): handler}).transport).ensure_app("a")
    assert seen == ["Bearer tok"]


def test_fly_assigns_missing_public_ips_only() -> None:
    m = Mock({("GET", "/v1/apps/a/ip_assignments"): {"ips": []}, ("POST", "/v1/apps/a/ip_assignments"): {"ip": "x"}})
    FlyApi("t", "o", transport=m.transport).ensure_public_ips("a")
    assert [c[2]["type"] for c in m.calls if c[0] == "POST"] == ["shared_v4", "v6"]
    have = Mock({("GET", "/v1/apps/a/ip_assignments"): {"ips": [{"ip": "66.1.1.1"}, {"ip": "2a09::1"}, {"ip": "9.9.9.9", "egress": True}]}})
    FlyApi("t", "o", transport=have.transport).ensure_public_ips("a")
    assert have.paths() == ["GET /v1/apps/a/ip_assignments"]


def test_fly_volume_is_created_once() -> None:
    m = Mock({("GET", "/v1/apps/a/volumes"): [{"name": "data", "state": "created"}]})
    FlyApi("t", "o", transport=m.transport).ensure_volume("a", "data", 1, "fra")
    assert m.paths() == ["GET /v1/apps/a/volumes"]
    m = Mock({("GET", "/v1/apps/a/volumes"): [], ("POST", "/v1/apps/a/volumes"): {"id": "v"}})
    FlyApi("t", "o", transport=m.transport).ensure_volume("a", "data", 2, "fra")
    assert m.calls[1][2]["size_gb"] == 2 and m.calls[1][2]["region"] == "fra"


def test_fly_machine_config_and_reuse() -> None:
    m = Mock(
        {
            ("GET", "/v1/apps/a/machines"): [],
            ("POST", "/v1/apps/a/machines"): {"id": "m1"},
            ("GET", "/v1/apps/a/machines/m1/wait"): {"ok": True},
        }
    )
    assert FlyApi("t", "o", transport=m.transport).ensure_machine("a", SPEC, "fra") == "m1"
    cfg = m.calls[1][2]["config"]
    assert cfg["image"] == "ghcr.io/x/app:1.0.0" and cfg["mounts"] == [{"volume": "data", "path": "/data"}]
    assert cfg["guest"]["memory_mb"] == 512 and cfg["metrics"] == {"port": 9091, "path": "/metrics"}
    assert cfg["services"][0]["internal_port"] == 8000 and cfg["services"][0]["checks"][0]["grace_period"] == "60s"
    again = Mock({("GET", "/v1/apps/a/machines"): [{"id": "m9", "state": "started"}]})
    assert FlyApi("t", "o", transport=again.transport).ensure_machine("a", SPEC, "fra") == "m9"
    assert again.paths() == ["GET /v1/apps/a/machines"]


def test_fly_wait_timeout_is_retryable() -> None:
    m = Mock({("POST", "/v1/apps/a/machines/m1/restart"): {}, ("GET", "/v1/apps/a/machines/m1/wait"): (408, {})})
    with pytest.raises(AdapterError):
        FlyApi("t", "o", transport=m.transport).restart("a", "m1")


def test_fly_write_files_pipes_base64_and_sets_owner() -> None:
    m = Mock({("POST", "/v1/apps/a/machines/m1/exec"): {"exit_code": 0, "stdout": "", "stderr": ""}})
    FlyApi("t", "o", transport=m.transport).write_files("a", "m1", {"/data/branding/x.css": b"abc", "/data/provision.json": b"{}"}, "zaci")
    first = m.calls[0][2]
    assert first["stdin"] == base64.b64encode(b"abc").decode()
    assert "base64 -d > /data/branding/x.css" in first["command"][2] and "chown zaci /data/branding/x.css" in first["command"][2]
    assert "/data/provision.json" in m.calls[1][2]["command"][2]  # payload jde po brandingu


def test_fly_write_failure_is_retryable() -> None:
    m = Mock({("POST", "/v1/apps/a/machines/m1/exec"): {"exit_code": 1, "stderr": "no space"}})
    with pytest.raises(AdapterError, match="no space"):
        FlyApi("t", "o", transport=m.transport).write_files("a", "m1", {"/data/x": b"1"}, "zaci")


def test_fly_read_file() -> None:
    ok = Mock({("POST", "/v1/apps/a/machines/m1/exec"): {"exit_code": 0, "stdout": base64.b64encode(b"hi").decode()}})
    assert FlyApi("t", "o", transport=ok.transport).read_file("a", "m1", "/x") == b"hi"
    missing = Mock({("POST", "/v1/apps/a/machines/m1/exec"): {"exit_code": 1, "stdout": ""}})
    assert FlyApi("t", "o", transport=missing.transport).read_file("a", "m1", "/x") is None


def test_fly_stop_start_destroy() -> None:
    m = Mock(
        {
            ("GET", "/v1/apps/a/machines"): [{"id": "m1", "state": "started"}, {"id": "m0", "state": "destroyed"}],
            ("POST", "/v1/apps/a/machines/m1/stop"): {"ok": True},
            ("POST", "/v1/apps/a/machines/m1/start"): {},
            ("GET", "/v1/apps/a/machines/m1/wait"): {"ok": True},
            ("DELETE", "/v1/apps/a"): (404, {}),
        }
    )
    fly = FlyApi("t", "o", transport=m.transport)
    fly.stop_machine("a")
    fly.start_machine("a")
    fly.destroy_app("a")  # 404 = už neexistuje, není chyba
    assert "POST /v1/apps/a/machines/m1/stop" in m.paths() and "DELETE /v1/apps/a" in m.paths()


def test_fly_certificate_only_when_missing() -> None:
    m = Mock({("GET", "/v1/apps/a/certificates/h.cz"): (404, {}), ("POST", "/v1/apps/a/certificates/acme"): (201, {})})
    FlyApi("t", "o", transport=m.transport).ensure_certificate("a", "h.cz")
    assert m.calls[1][2] == {"hostname": "h.cz"}


def test_fly_logs_failure_returns_empty() -> None:
    m = Mock({("GET", "/api/v1/apps/a/logs"): (500, {})})
    assert FlyApi("t", "o", transport=m.transport).recent_logs("a") == []


def test_error_classification() -> None:
    fly = FlyApi(
        "t", "o", transport=Mock({("GET", "/v1/apps/a"): (503, {}), ("GET", "/v1/apps/b"): (401, {"error": "bad token"})}).transport
    )
    with pytest.raises(AdapterError):
        fly.ensure_app("a")
    with pytest.raises(PermanentError, match="401"):
        fly.ensure_app("b")

    def down(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route")

    with pytest.raises(AdapterError):
        FlyApi("t", "o", transport=httpx2.MockTransport(down)).ensure_app("a")


# --- Cloudflare DNS ---

ZONE = "/client/v4/zones/z/dns_records"


def test_dns_create_update_and_noop() -> None:
    m = Mock({("GET", ZONE): {"success": True, "result": []}, ("POST", ZONE): {"success": True, "result": {}}})
    CloudflareDns("t", "z", transport=m.transport).ensure_cname("a.apps.cz", "vm-a.fly.dev")
    assert m.calls[1][2] == {"type": "CNAME", "name": "a.apps.cz", "content": "vm-a.fly.dev", "proxied": False, "ttl": 1}

    rec = {"id": "r1", "type": "CNAME", "content": "old.fly.dev"}
    m = Mock({("GET", ZONE): {"result": [rec]}, ("PUT", f"{ZONE}/r1"): {"result": {}}})
    CloudflareDns("t", "z", transport=m.transport).ensure_cname("a.apps.cz", "vm-a.fly.dev")
    assert "PUT /client/v4/zones/z/dns_records/r1" in m.paths()

    same = Mock({("GET", ZONE): {"result": [{**rec, "content": "vm-a.fly.dev"}]}})
    CloudflareDns("t", "z", transport=same.transport).ensure_cname("a.apps.cz", "vm-a.fly.dev")
    assert len(same.calls) == 1


def test_dns_refuses_to_overwrite_other_record_type_and_deletes_cname() -> None:
    m = Mock({("GET", ZONE): {"result": [{"id": "r1", "type": "A", "content": "1.2.3.4"}]}})
    with pytest.raises(PermanentError, match="už existuje"):
        CloudflareDns("t", "z", transport=m.transport).ensure_cname("a.apps.cz", "x")
    d = Mock({("GET", ZONE): {"result": [{"id": "r1", "type": "CNAME", "content": "x"}]}, ("DELETE", f"{ZONE}/r1"): {}})
    CloudflareDns("t", "z", transport=d.transport).delete_cname("a.apps.cz")
    assert "DELETE /client/v4/zones/z/dns_records/r1" in d.paths()


# --- R2 ---

ACC = "/client/v4/accounts/acc"


def test_r2_creates_bucket_scoped_token_and_derives_s3_keys() -> None:
    m = Mock(
        {
            ("POST", f"{ACC}/r2/buckets"): {"success": True, "result": {}},
            ("GET", f"{ACC}/tokens"): {"result": [{"id": "old", "name": "vmarket-vm-a"}, {"id": "other", "name": "jiny"}]},
            ("DELETE", f"{ACC}/tokens/old"): {},
            ("GET", f"{ACC}/tokens/permission_groups"): {"result": [{"id": "g1", "name": "Workers R2 Storage Bucket Item Write"}]},
            ("POST", f"{ACC}/tokens"): {"success": True, "result": {"id": "tid", "value": "secret-value"}},
        }
    )
    s = R2Store("t", "acc", transport=m.transport).ensure_backup_bucket("vm-a").values
    assert s["LITESTREAM_ACCESS_KEY_ID"] == "tid"
    assert s["LITESTREAM_SECRET_ACCESS_KEY"] == hashlib.sha256(b"secret-value").hexdigest()
    assert s["LITESTREAM_S3_ENDPOINT"] == "https://acc.r2.cloudflarestorage.com" and s["LITESTREAM_REPLICA_URL"] == "s3://vm-a/db"
    assert "DELETE /client/v4/accounts/acc/tokens/old" in m.paths() and "DELETE /client/v4/accounts/acc/tokens/other" not in m.paths()
    policy = m.calls[-1][2]["policies"][0]
    assert policy["resources"] == {"com.cloudflare.edge.r2.bucket.acc_default_vm-a": "*"}
    assert policy["permission_groups"] == [{"id": "g1"}]


def test_r2_existing_bucket_is_fine_and_unknown_permission_is_permanent() -> None:
    routes: dict[tuple[str, str], Any] = {
        ("POST", f"{ACC}/r2/buckets"): (409, {"success": False}),
        ("GET", f"{ACC}/tokens"): {"result": []},
        ("GET", f"{ACC}/tokens/permission_groups"): {"result": []},
    }
    with pytest.raises(PermanentError, match="oprávnění"):
        R2Store("t", "acc", transport=Mock(routes).transport).ensure_backup_bucket("vm-a")


def test_r2_eu_jurisdiction_endpoint_and_header() -> None:
    seen: list[str | None] = []

    def bucket(r: httpx2.Request) -> httpx2.Response:
        seen.append(r.headers.get("cf-r2-jurisdiction"))
        return httpx2.Response(200, json={"success": True, "result": {}})

    r2 = R2Store("t", "acc", jurisdiction="eu", transport=Mock({("POST", f"{ACC}/r2/buckets"): bucket, ("GET", f"{ACC}/tokens"): {"result": []},
        ("GET", f"{ACC}/tokens/permission_groups"): {"result": [{"id": "g", "name": "Workers R2 Storage Bucket Item Write"}]},
        ("POST", f"{ACC}/tokens"): {"result": {"id": "i", "value": "v"}}}).transport)  # fmt: skip
    s = r2.ensure_backup_bucket("vm-a").values
    assert seen == ["eu"] and s["LITESTREAM_S3_ENDPOINT"] == "https://acc.eu.r2.cloudflarestorage.com"


# --- SMTP ---


class FakeServer:
    """Napodobuje smtplib.SMTP / SMTP_SSL; chování řídí atributy třídy."""

    login_error: Exception | None = None
    connect_error: Exception | None = None
    log: list[str] = []  # noqa: RUF012

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        if self.connect_error:
            raise self.connect_error
        self.log.append(f"connect {host}:{port} {type(self).__name__}")

    def __enter__(self) -> "FakeServer":
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self, context: object = None) -> None:
        self.log.append("starttls")

    def login(self, user: str, password: str) -> None:
        if self.login_error:
            raise self.login_error
        self.log.append(f"login {user}")


class FakeServerSsl(FakeServer):
    pass


@pytest.fixture
def smtp_env(monkeypatch: pytest.MonkeyPatch) -> type[FakeServer]:
    FakeServer.login_error = None
    FakeServer.connect_error = None
    FakeServer.log = []
    monkeypatch.setattr(smtplib, "SMTP", FakeServer)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeServerSsl)
    return FakeServer


def test_smtp_check_587_uses_starttls_and_465_implicit_tls(smtp_env: type[FakeServer]) -> None:
    SmtpChecker().check("h.cz", 587, "u", "p")
    assert smtp_env.log == ["connect h.cz:587 FakeServer", "starttls", "login u"]
    smtp_env.log.clear()
    SmtpChecker().check("h.cz", 465, "u", "p")
    assert smtp_env.log == ["connect h.cz:465 FakeServerSsl", "login u"]


def test_smtp_check_error_classification(smtp_env: type[FakeServer]) -> None:
    smtp_env.login_error = smtplib.SMTPAuthenticationError(535, b"bad")
    with pytest.raises(PermanentError, match="přihlášení odmítnuto"):
        SmtpChecker().check("h.cz", 587, "u", "p")
    smtp_env.login_error = None
    smtp_env.connect_error = TimeoutError("timed out")
    with pytest.raises(AdapterError, match="spojení selhalo"):
        SmtpChecker().check("h.cz", 587, "u", "p")
    smtp_env.connect_error = ssl.SSLCertVerificationError(1, "hostname mismatch")
    with pytest.raises(PermanentError, match="certifikát"):
        SmtpChecker().check("h.cz", 587, "u", "p")


# --- Probe a factory ---


def test_probe_returns_status_or_none() -> None:
    assert HttpProbe(transport=httpx2.MockTransport(lambda r: httpx2.Response(503))).get("https://x") == 503

    def down(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("x")

    assert HttpProbe(transport=httpx2.MockTransport(down)).get("https://x") is None


def test_factory_lists_all_missing_variables() -> None:
    with pytest.raises(factory.ConfigError) as e:
        factory.from_env({"FLY_API_TOKEN": "t"})
    assert all(k in str(e.value) for k in factory.REQUIRED if k != "FLY_API_TOKEN")
    full = {k: "x" for k in factory.REQUIRED}
    assert factory.from_env(full).fly is not None
