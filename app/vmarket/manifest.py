"""Načtení a validace Manifestu `vmarket.yaml` (formát v1, viz docs/app-contract.md)."""

from dataclasses import dataclass
from typing import Any

import yaml

INPUT_TYPES = {"text", "email", "url", "number", "money", "boolean", "image", "color_tokens", "table"}
TARGET_PREFIXES = ("payload:", "branding:")


class ManifestError(ValueError):
    """Manifest je nevalidní; zpráva říká, kde."""


@dataclass(frozen=True)
class PlanSpec:
    key: str
    name: str
    interval: str
    price_czk: int | None


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    description: str
    image: str
    plans: list[PlanSpec]
    raw: dict[str, Any]


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ManifestError(msg)


def _text(d: dict[str, Any], key: str, where: str) -> str:
    value = d.get(key)
    _require(isinstance(value, str) and bool(value.strip()), f"{where}{key}: povinný neprázdný text")
    return str(value).strip()


def _check_inputs(inputs: list[Any], where: str, seen: set[str], *, relative_targets: bool = False) -> None:
    for i, item in enumerate(inputs):
        w = f"{where}[{i}]"
        _require(isinstance(item, dict), f"{w}: musí být objekt")
        input_id = _text(item, "id", f"{w}.")
        _require(input_id not in seen, f"{w}.id: duplicitní '{input_id}'")
        seen.add(input_id)
        _require(item.get("type") in INPUT_TYPES, f"{w}.type: neznámý typ {item.get('type')!r}")
        _text(item, "label", f"{w}.")
        target = item.get("target")
        if relative_targets:  # sloupec tabulky: target je relativní k řádku
            _require(isinstance(target, str) and bool(target), f"{w}.target: povinný")
        else:
            _require(
                (isinstance(target, str) and target.startswith(TARGET_PREFIXES)) or item["type"] in {"color_tokens", "table"},
                f"{w}.target: musí začínat payload: nebo branding:",
            )
        if item["type"] == "table":
            _check_inputs(item.get("columns") or [], f"{w}.columns", set(), relative_targets=True)


def parse(text: str) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ManifestError(f"Neplatné YAML: {e}") from e
    _require(isinstance(data, dict), "Manifest musí být objekt")
    _require(data.get("manifest_version") == 1, "manifest_version: podporovaná je jen 1")
    app_id = _text(data, "id", "")
    _require(app_id.replace("-", "").isalnum() and app_id == app_id.lower(), "id: jen a-z, číslice a pomlčky")
    image = _text(data, "image", "")
    _require(":" not in image.rsplit("/", 1)[-1], "image: bez tagu, tag doplňuje Release")

    resources = data.get("resources")
    _require(isinstance(resources, dict), "resources: povinné")
    _require(resources.get("machines") == 1, "resources.machines: musí být 1")
    for key in ("memory_mb", "volume_gb", "port"):
        _require(isinstance(resources.get(key), int) and resources[key] > 0, f"resources.{key}: kladné celé číslo")

    plans = []
    for i, p in enumerate(data.get("plans") or []):
        w = f"plans[{i}]."
        price = p.get("price_czk")
        _require(price is None or (isinstance(price, int) and price >= 0), f"{w}price_czk: celé číslo nebo null")
        _require(p.get("interval") in {"month", "year"}, f"{w}interval: month nebo year")
        plans.append(PlanSpec(_text(p, "id", w), _text(p, "name", w), p["interval"], price))
    _require(bool(plans), "plans: aspoň jeden Plan")
    _require(len({p.key for p in plans}) == len(plans), "plans: duplicitní id")

    _check_inputs(data.get("inputs") or [], "inputs", set())
    return Manifest(app_id, _text(data, "name", ""), _text(data, "description", ""), image, plans, data)
