import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from tests.fakes import TOKENS
from vmarket import manifest
from vmarket.provisioning import payload
from vmarket.provisioning.ports import PermanentError

M = manifest.parse((Path(__file__).parent / "fixtures" / "autoskola.vmarket.yaml").read_text())


def png(w: int = 600, h: int = 300) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 20, 30, 0)).save(out, "PNG")
    return out.getvalue()


def good_inputs() -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "school_name": "Autoškola Testová",
        "logo": png(),
        "colors": {"primary": "#7b2cbf", "accent": "#2ec4b6"},
        "operator_email": "a@b.cz",
        "operator_person_name": "Test Provozovatel",
    }
    required = {i["id"]: i for i in M.raw["inputs"] if i.get("required")}
    samples = {
        "operator_name": "Testová s.r.o.",
        "operator_ico": "12345678",
        "operator_address": "Testovací 1, Praha",
        "phone": "+420 777 000 999",
        "bank_account": "19-2000145399/0800",
        "terms_url": "https://t.example/a",
        "privacy_url": "https://t.example/b",
        "withdrawal_url": "https://t.example/c",
    }
    for key in required:
        if key in samples:
            inputs[key] = samples[key]
    inputs["courses"] = [
        {
            "course_slug": "kurz-b",
            "course_name": "Řidičák B",
            "variant_key": "zaklad",
            "variant_name": "Základ",
            "variant_price": 19000,
            "variant_hours": 28,
        },
        {
            "course_slug": "kurz-b",
            "course_name": "Řidičák B",
            "variant_key": "plus",
            "variant_name": "Plus",
            "variant_price": 25000,
            "variant_hours": 36,
        },
    ]
    return inputs


def build(inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    return payload.build(M, inputs, TOKENS)


def test_builds_payload_and_branding() -> None:
    p, files = build(good_inputs())
    assert p["version"] == 1
    assert p["operator"] == {"email": "a@b.cz", "name": "Test Provozovatel"}
    assert len(p["courses"]) == 1
    assert [v["key"] for v in p["courses"][0]["variants"]] == ["zaklad", "plus"]
    assert json.loads(files["branding.json"]) == {"name": "Autoškola Testová"}
    css = files["tokens.css"].decode()
    assert "--color-primary: #7b2cbf;" in css and "--color-accent: #2ec4b6; /* komentář */" in css
    assert set(files) == {"branding.json", "tokens.css", "logo-email.jpg", "ikona-32.png", "ikona-180.png"}
    assert Image.open(io.BytesIO(files["ikona-180.png"])).size == (180, 180)
    assert Image.open(io.BytesIO(files["logo-email.jpg"])).format == "JPEG"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"school_name": None}, "school_name"),
        ({"school_name": "A"}, "délka"),
        ({"operator_email": "není e-mail"}, "e-mail"),
        ({"terms_url": "ftp://x"}, "schéma"),
        ({"logo": png(100, 100)}, "užší"),
        ({"logo": b"<svg/>"}, "nejde přečíst"),
        ({"colors": {"primary": "červená", "accent": "#2ec4b6"}}, "#rrggbb"),
        ({"neznamy": 1}, "Neznámé"),
        ({"courses": []}, "řádků"),
    ],
)
def test_rejects_bad_inputs(change: dict[str, Any], message: str) -> None:
    inputs = {**good_inputs(), **change}
    with pytest.raises(PermanentError, match=message):
        build(inputs)


def test_missing_token_in_default_css_is_error() -> None:
    with pytest.raises(PermanentError, match="token"):
        payload.build(M, good_inputs(), ":root{}")
