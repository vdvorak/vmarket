import sqlite3
from pathlib import Path

import pytest

from vmarket import db, manifest, repo

FIXTURE = Path(__file__).parent / "fixtures" / "autoskola.vmarket.yaml"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


@pytest.fixture
def instance(conn: sqlite3.Connection) -> int:
    repo.register_app(conn, manifest.parse(FIXTURE.read_text()))
    customer = repo.get_or_create_customer(conn, "a@b.cz")
    return repo.create_comped_instance(conn, app_id="autoskola", customer_id=customer, slug="testova")


def state(conn: sqlite3.Connection, instance_id: int) -> str:
    return str(conn.execute("SELECT state FROM instances WHERE id = ?", (instance_id,)).fetchone()["state"])


def test_real_manifest_registers_app_and_plans(conn: sqlite3.Connection) -> None:
    m = manifest.parse(FIXTURE.read_text())
    repo.register_app(conn, m)
    repo.register_app(conn, m)  # idempotentní
    assert conn.execute("SELECT count(*) FROM apps").fetchone()[0] == 1
    plan = conn.execute("SELECT * FROM plans").fetchone()
    assert plan["plan_key"] == "monthly" and plan["price_czk"] is None


def test_happy_path_and_event_log(conn: sqlite3.Connection, instance: int) -> None:
    for s in ("provisioning", "running", "deleted"):
        repo.transition(conn, instance, s)
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM event_log WHERE instance_id = ? ORDER BY id", (instance,))]
    assert kinds == ["instance.created", "instance.provisioning", "instance.running", "instance.deleted"]
    assert conn.execute("SELECT deleted_at FROM instances WHERE id = ?", (instance,)).fetchone()[0] is not None


def test_invalid_transition_changes_nothing(conn: sqlite3.Connection, instance: int) -> None:
    with pytest.raises(repo.TransitionError):
        repo.transition(conn, instance, "running")
    assert state(conn, instance) == "pending"


def test_failed_keeps_reason_and_retry_clears_it(conn: sqlite3.Connection, instance: int) -> None:
    repo.transition(conn, instance, "provisioning")
    repo.transition(conn, instance, "failed", "health check timeout")
    assert conn.execute("SELECT failure_reason FROM instances").fetchone()[0] == "health check timeout"
    repo.transition(conn, instance, "provisioning")
    assert conn.execute("SELECT failure_reason FROM instances").fetchone()[0] is None


def test_deleted_is_terminal(conn: sqlite3.Connection, instance: int) -> None:
    repo.transition(conn, instance, "deleted")
    with pytest.raises(repo.TransitionError):
        repo.transition(conn, instance, "provisioning")


def test_comped_never_suspends(conn: sqlite3.Connection, instance: int) -> None:
    repo.transition(conn, instance, "provisioning")
    repo.transition(conn, instance, "running")
    with pytest.raises(repo.TransitionError):
        repo.transition(conn, instance, "suspended")


def test_paid_instance_requires_plan_and_comped_forbids_it(conn: sqlite3.Connection, instance: int) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO instances (app_id, customer_id, slug, billing_mode) VALUES ('autoskola', 1, 'x', 'paid')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO instances (app_id, customer_id, slug, billing_mode, plan_id) VALUES ('autoskola', 1, 'y', 'comped', 1)")


def test_slug_must_be_dns_safe(conn: sqlite3.Connection, instance: int) -> None:
    for bad in ("Velke", "a_b", "-x"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO instances (app_id, customer_id, slug, billing_mode) VALUES ('autoskola', 1, ?, 'comped')", (bad,))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda t: t.replace("manifest_version: 1", "manifest_version: 2"), "manifest_version"),
        (lambda t: t.replace("machines: 1", "machines: 2"), "machines"),
        (lambda t: t.replace("image: ghcr.io/vdvorak/autoskola-app ", "image: ghcr.io/vdvorak/autoskola-app:1.0 "), "bez tagu"),
        (lambda t: t.replace("target: branding:logo-email.jpg", "target: nikam"), "target"),
        (lambda t: t.replace("target: secret:SMTP_PASSWORD", "target: secret:JINE"), "SMTP_PASSWORD"),
    ],
)
def test_manifest_rejects(mutate: object, message: str) -> None:
    with pytest.raises(manifest.ManifestError, match=message):
        manifest.parse(mutate(FIXTURE.read_text()))  # type: ignore[operator]
