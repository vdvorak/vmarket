import json
import sqlite3
from pathlib import Path

import pytest

from tests import fakes
from tests.test_payload import good_inputs, png
from vmarket import cli, db
from vmarket.provisioning.engine import Config
from vmarket.provisioning.ports import AdapterError

FIXTURE = Path(__file__).parent / "fixtures" / "autoskola.vmarket.yaml"


class Cli:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.conn = db.connect(tmp_path / "t.db")
        db.migrate(self.conn)
        self.world = fakes.World()
        self.config = Config(base_domain="apps.example.cz", sleep=lambda _: None, health_attempts=2)
        self.lines: list[str] = []

    def __call__(self, *argv: str) -> int:
        return cli.run(argv, self.conn, lambda: fakes.make(self.world), self.config, self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def inputs_file(self) -> Path:
        data = {k: v for k, v in good_inputs().items() if k != "logo"}
        (self.tmp / "logo.png").write_bytes(png())
        data["logo"] = {"$file": "logo.png"}
        path = self.tmp / "inputs.json"
        path.write_text(json.dumps(data))
        return path

    def state(self, slug: str = "testova") -> str:
        row = self.conn.execute("SELECT state FROM instances WHERE slug = ?", (slug,)).fetchone()
        return str(row["state"])


@pytest.fixture
def c(tmp_path: Path) -> Cli:
    cl = Cli(tmp_path)
    assert cl("app", "register", str(FIXTURE)) == 0
    assert cl("release", "add", "autoskola", "1.0.0") == 0
    return cl


def comp(c: Cli) -> int:
    return c("instance", "comp", "--app", "autoskola", "--slug", "testova", "--email", "a@b.cz", "--inputs", str(c.inputs_file()))


def test_register_and_comp_provisions_end_to_end(c: Cli) -> None:
    assert comp(c) == 0
    assert c.state() == "running"
    assert "Provisioning: running" in c.text and "krok verify_import" in c.text
    assert "/data/provision.json.done" in c.world.files


def test_status_overview_and_detail(c: Cli) -> None:
    comp(c)
    c.lines.clear()
    assert c("status") == 0
    assert "testova" in c.text and "running" in c.text and "comped" in c.text
    c.lines.clear()
    assert c("status", "testova") == 0
    assert "Fly app: vm-autoskola-testova" in c.text and "instance.running" in c.text


def test_failed_provisioning_shows_reason_and_can_be_retried(c: Cli) -> None:
    c.world.fail["ensure_machine"] = 99
    comp(c)
    assert c.state() == "failed" and "Selhání:" in c.text
    c.world.fail["ensure_machine"] = 0
    assert c("instance", "provision", "testova") == 0
    assert c.state() == "running"


def test_comped_cannot_be_suspended(c: Cli) -> None:
    comp(c)
    assert c("instance", "suspend", "testova") == 1
    assert "Comped" in c.text and c.state() == "running" and c.world.calls["stop_machine"] == 0


def test_suspend_resume_work_for_paid_instance(c: Cli) -> None:
    comp(c)
    c.conn.execute("UPDATE instances SET billing_mode = 'paid', plan_id = 1 WHERE slug = 'testova'")  # simulace placené
    assert c("instance", "suspend", "testova") == 0 and c.state() == "suspended"
    assert c("instance", "resume", "testova") == 0 and c.state() == "running"
    assert c.world.calls["stop_machine"] == 1 and c.world.calls["start_machine"] == 1


def test_delete_destroys_app_and_dns(c: Cli) -> None:
    comp(c)
    assert c("instance", "delete", "testova") == 0
    assert c.state() == "deleted"
    assert c.world.calls["destroy_app"] == 1 and "testova.apps.example.cz" not in c.world.dns


def test_delete_of_never_provisioned_instance_touches_nothing(c: Cli) -> None:
    assert c("instance", "comp", "--app", "autoskola", "--slug", "testova", "--email", "a@b.cz", "--no-provision") == 0
    assert c("instance", "delete", "testova") == 0
    assert c.world.calls["destroy_app"] == 0 and c.state() == "deleted"


def test_service_error_on_delete_keeps_state(c: Cli) -> None:
    comp(c)

    c.world.fail["destroy_app"] = 1
    with pytest.raises(AdapterError):
        c("instance", "delete", "testova")
    assert c.state() == "running"
    assert c("instance", "delete", "testova") == 0


def test_errors_are_reported_not_raised(c: Cli) -> None:
    assert c("instance", "suspend", "neexistuje") == 1 and "neexistuje" in c.text
    assert c("app", "register", str(c.tmp / "nic.yaml")) == 1
    assert c("instance", "comp", "--app", "autoskola", "--slug", "Spatny_Slug", "--email", "a@b.cz", "--no-provision") == 1


def test_missing_adapter_config_is_reported(c: Cli) -> None:
    conn: sqlite3.Connection = c.conn
    lines: list[str] = []
    code = cli.run(
        ["instance", "comp", "--app", "autoskola", "--slug", "x", "--email", "a@b.cz"], conn, cli.default_adapters, out=lines.append
    )
    assert code == 1 and "Chybí proměnné prostředí" in lines[-1]


def test_custom_domain_is_shown_with_cname_instruction(c: Cli) -> None:
    assert (
        c(
            "instance",
            "comp",
            "--app",
            "autoskola",
            "--slug",
            "testova",
            "--email",
            "a@b.cz",
            "--inputs",
            str(c.inputs_file()),
            "--domain",
            "Zaci.Ubouraka.cz",
        )
        == 0
    )
    c.lines.clear()
    c("status", "testova")
    assert "CNAME zaci.ubouraka.cz → vm-autoskola-testova.fly.dev" in c.text


def test_invalid_custom_domain_is_rejected(c: Cli) -> None:
    assert (
        c(
            "instance",
            "comp",
            "--app",
            "autoskola",
            "--slug",
            "x",
            "--email",
            "a@b.cz",
            "--no-provision",
            "--domain",
            "https://zaci.ubouraka.cz/",
        )
        == 1
    )
    assert "Neplatná doména" in c.text
