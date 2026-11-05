import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests import fakes
from tests.test_payload import good_inputs
from vmarket import db, manifest, repo
from vmarket.provisioning.engine import Config, provision
from vmarket.provisioning.ports import Adapters

FIXTURE = Path(__file__).parent / "fixtures" / "autoskola.vmarket.yaml"


def encode(inputs: dict[str, Any]) -> dict[str, Any]:
    return {k: ({"$base64": base64.b64encode(v).decode()} if isinstance(v, bytes) else v) for k, v in inputs.items()}


class Env:
    def __init__(self, tmp_path: Path, inputs: dict[str, Any] | None = None, release: bool = True) -> None:
        self.conn = db.connect(tmp_path / "t.db")
        db.migrate(self.conn)
        repo.register_app(self.conn, manifest.parse(FIXTURE.read_text()))
        if release:
            repo.add_release(self.conn, app_id="autoskola", tag="1.0.0")
        customer = repo.get_or_create_customer(self.conn, "a@b.cz")
        self.id = repo.create_comped_instance(
            self.conn, app_id="autoskola", customer_id=customer, slug="testova", inputs=encode(inputs or good_inputs())
        )
        self.world = fakes.World()
        self.adapters: Adapters = fakes.make(self.world)
        self.sleeps: list[float] = []
        self.config = Config(base_domain="apps.example.cz", sleep=self.sleeps.append, health_attempts=3)

    def run(self) -> str:
        return provision(self.conn, self.id, self.adapters, self.config)

    def row(self) -> sqlite3.Row:
        row: sqlite3.Row = self.conn.execute("SELECT * FROM instances WHERE id = ?", (self.id,)).fetchone()
        return row

    def steps(self, status: str) -> list[str]:
        q = "SELECT step FROM provisioning_steps WHERE instance_id = ? AND status = ? ORDER BY rowid"
        return [r[0] for r in self.conn.execute(q, (self.id, status))]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_happy_path(env: Env) -> None:
    assert env.run() == "running"
    w = env.world
    assert env.row()["state"] == "running" and env.row()["fly_app_name"] == "vm-autoskola-testova"
    assert w.dns == {"testova.apps.example.cz": "vm-autoskola-testova.fly.dev"}
    assert (
        w.secrets["SMTP_HOST"] == "smtp.testova.example"
        and w.secrets["SMTP_PASSWORD"] == "tajne-heslo"
        and w.secrets["LITESTREAM_REPLICA_URL"] == "s3://vm-autoskola-testova"
    )
    assert "/data/provision.json.done" in w.files and "/data/provision.json" not in w.files
    assert json.loads(w.files["/data/branding/branding.json"])["name"] == "Autoškola Testová"
    assert w.spec is not None and w.spec.image == "ghcr.io/vdvorak/autoskola-app:1.0.0" and w.spec.memory_mb == 512
    assert w.spec.env["APP_URL"] == "https://testova.apps.example.cz"
    assert env.steps("done")[-1] == "verify_import" and len(env.steps("done")) == 12


def test_retries_transient_errors_with_backoff(env: Env) -> None:
    env.world.fail["ensure_volume"] = 2
    assert env.run() == "running"
    assert env.world.calls["ensure_volume"] == 3
    assert env.sleeps[:2] == [2.0, 4.0]


def test_gives_up_after_retries_then_resumes_without_repeating_done_steps(env: Env) -> None:
    env.world.fail["ensure_machine"] = 99
    assert env.run() == "failed"
    assert "ensure_machine" in env.row()["failure_reason"] or "machine" in env.row()["failure_reason"]
    assert env.steps("done") == ["r2_bucket", "smtp_check", "fly_app", "public_ips", "fly_volume", "fly_secrets", "dns"]
    before = dict(env.world.calls)

    env.world.fail["ensure_machine"] = 0
    assert env.run() == "running"
    # už hotové kroky se nezopakovaly
    for call in ("ensure_backup_bucket", "smtp_check", "ensure_app", "ensure_volume", "set_secrets", "ensure_cname"):
        assert env.world.calls[call] == before[call]
    assert env.row()["failure_reason"] is None


def test_crash_in_middle_resumes_from_first_unfinished_step(env: Env) -> None:
    class BoomError(Exception):
        pass

    def explode(*a: object, **k: object) -> None:
        raise BoomError

    env.adapters.fly.write_files = explode  # type: ignore[method-assign]
    with pytest.raises(BoomError):
        env.run()
    assert env.row()["state"] == "failed" and "Interní chyba" in env.row()["failure_reason"]
    env.adapters = fakes.make(env.world)
    assert env.run() == "running"
    assert env.world.calls["ensure_backup_bucket"] == 1


def test_invalid_inputs_fail_without_retry_and_before_writing_files(env: Env) -> None:
    env.conn.execute("UPDATE instances SET inputs_json = ?", (json.dumps(encode({**good_inputs(), "operator_email": "x"})),))
    assert env.run() == "failed"
    assert "e-mail" in env.row()["failure_reason"]
    assert env.world.calls["write_files"] == 0 and env.world.calls["restart"] == 0


def test_app_rejecting_payload_fails_with_cause_from_logs(env: Env) -> None:
    # Fake aplikace odmítne payload s jinou verzí; simulujeme to přepsáním konstanty v Manifestu.
    raw = json.loads(env.conn.execute("SELECT manifest_json FROM apps").fetchone()[0])
    raw["payload_constants"]["version"] = 2
    env.conn.execute("UPDATE apps SET manifest_json = ?", (json.dumps(raw),))
    assert env.run() == "failed"
    assert "Provisioning Payload nejde naimportovat" in env.row()["failure_reason"]
    assert env.world.calls["probe"] == 3  # nečekalo se zbytečně dál, žádný druhý pokus


def test_health_never_ok_without_payload_error_is_retried_then_fails(env: Env) -> None:
    env.adapters.fly.restart = lambda *a, **k: None  # type: ignore[method-assign]
    assert env.run() == "failed"
    assert env.world.calls["probe"] == 6  # 2 pokusy kroku health × 3 dotazy
    assert "Health check" in env.row()["failure_reason"]


def test_no_release_fails_clearly(tmp_path: Path) -> None:
    e = Env(tmp_path, release=False)
    assert e.run() == "failed"
    assert "Release" in e.row()["failure_reason"]


def test_running_instance_cannot_be_provisioned_again(env: Env) -> None:
    env.run()
    with pytest.raises(repo.TransitionError):
        env.run()


def test_events_are_logged(env: Env) -> None:
    env.run()
    kinds = [r[0] for r in env.conn.execute("SELECT kind FROM event_log WHERE instance_id = ?", (env.id,))]
    assert kinds.count("provision.step") == 12 and kinds[-1] == "instance.running"


def test_smtp_is_checked_with_customers_credentials(env: Env) -> None:
    env.run()
    assert env.adapters.smtp.checked == [("smtp.testova.example", 587, "info@testova.example", "tajne-heslo")]  # type: ignore[attr-defined]


def test_bad_smtp_credentials_fail_before_anything_is_deployed(env: Env) -> None:
    env.world.smtp_error = "SMTP smtp.testova.example:587: přihlášení odmítnuto (535)"
    assert env.run() == "failed"
    assert "535" in env.row()["failure_reason"]
    assert env.world.calls["ensure_app"] == 0 and env.world.calls["ensure_machine"] == 0


def test_smtp_secrets_are_erased_from_db_only_after_success(env: Env) -> None:
    env.world.fail["ensure_machine"] = 99
    env.run()
    assert "smtp_password" in json.loads(env.row()["inputs_json"])  # po selhání je potřeba k opakování
    env.world.fail["ensure_machine"] = 0
    assert env.run() == "running"
    stored = json.loads(env.row()["inputs_json"])
    assert "smtp_password" not in stored and "smtp_host" not in stored and stored["school_name"] == "Autoškola Testová"
