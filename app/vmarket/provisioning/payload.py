"""Z Inputs podle Manifestu sestaví Provisioning Payload (provision.json) a soubory brandingu.

`target` každého Inputu říká, kam hodnota jde (docs/app-contract.md, Manifest):
- `payload:<tečková.cesta>` klíč v payloadu; u tabulky se řádky seskupí podle prvního sloupce a sloupce s cílem
  `pole[].klíč` tvoří položky pole `pole` (autoškola: řádky se stejným course_slug jsou jeden Kurz s variants[]),
- `branding:<soubor>` soubor v branding_dir, `branding:<soubor>#klíč` klíč v JSON souboru,
- `color_tokens` přepíše tokeny v výchozím tokens.css aplikace, `image` s cílem logo-email.jpg vyrobí i ikony.
Hodnoty se kontrolují podle `validation` v Manifestu (podmnožina: délky, pattern, min/max, integer, one_of, schemes,
obrázky); aplikace je kontroluje znovu při importu."""

import io
import json
import re
from typing import Any

from PIL import Image, UnidentifiedImageError

from vmarket.manifest import Manifest
from vmarket.provisioning.ports import PermanentError

ICONS = {"ikona-32.png": 32, "ikona-180.png": 180, "ikona-192.png": 192, "ikona-512.png": 512}
LOGO = "logo-email.jpg"
TOKENS = "tokens.css"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _fail(where: str, msg: str) -> PermanentError:
    return PermanentError(f"Input {where}: {msg}")


def _check_scalar(item: dict[str, Any], value: Any, where: str) -> None:
    v = item.get("validation") or {}
    kind = item["type"]
    if kind in {"text", "email", "url", "secret"}:
        if not isinstance(value, str):
            raise _fail(where, "očekávám text")
        if len(value) < v.get("min_length", 0) or len(value) > v.get("max_length", 10**9):
            raise _fail(where, f"délka mimo rozsah {v.get('min_length', 0)}–{v.get('max_length', '∞')}")
        if "pattern" in v and not re.search(v["pattern"], value):
            raise _fail(where, v.get("message", f"neodpovídá {v['pattern']}"))
        if kind == "url" and not value.startswith(tuple(v.get("schemes", ["https://", "http://"]))):
            raise _fail(where, "špatné schéma odkazu")
        if kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise _fail(where, "neplatný e-mail")
    elif kind in {"number", "money"}:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _fail(where, "očekávám číslo")
        if v.get("integer") and int(value) != value:
            raise _fail(where, "očekávám celé číslo")
        if value < v.get("min", float("-inf")) or value > v.get("max", float("inf")):
            raise _fail(where, f"mimo rozsah {v.get('min', '-∞')}–{v.get('max', '∞')}")
        if "one_of" in v and value not in v["one_of"]:
            raise _fail(where, f"povoleno jen {v['one_of']}")
    elif kind == "boolean" and not isinstance(value, bool):
        raise _fail(where, "očekávám ano/ne")


def _empty(value: Any) -> bool:
    return value is None or value == ""


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        root = root.setdefault(key, {})
    root[keys[-1]] = value


def _table(item: dict[str, Any], rows: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise _fail(where, "očekávám seznam řádků")
    v = item.get("validation") or {}
    if len(rows) < v.get("min_rows", 0) or len(rows) > v.get("max_rows", 10**9):
        raise _fail(where, f"počet řádků mimo rozsah {v.get('min_rows', 0)}–{v.get('max_rows', '∞')}")
    cols = item["columns"]
    items: dict[Any, dict[str, Any]] = {}
    for n, row in enumerate(rows):
        entry: dict[str, Any] = {}
        child: dict[str, Any] = {}
        list_key = ""
        for col in cols:
            w = f"{where}[{n}].{col['id']}"
            value = row.get(col["id"], col.get("default"))
            if _empty(value):
                if col.get("required"):
                    raise _fail(w, "chybí povinná hodnota")
                continue
            _check_scalar(col, value, w)
            target = col["target"]
            if "[]." in target:
                list_key, key = target.split("[].", 1)
                child[key] = value
            else:
                entry[target] = value
        group = row.get(cols[0]["id"]) if list_key else n
        parent = items.setdefault(group, entry)
        if list_key:
            parent.setdefault(list_key, []).append(child)
    return list(items.values())


def _image(item: dict[str, Any], value: Any, where: str) -> Image.Image:
    v = item.get("validation") or {}
    data = value if isinstance(value, bytes) else None
    if data is None:
        raise _fail(where, "očekávám obsah obrázku")
    if len(data) > v.get("max_size_kb", 10**9) * 1024:
        raise _fail(where, f"větší než {v['max_size_kb']} kB")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        # SVG a jiné nerastrové formáty zatím nejdou převést na ikony.
        raise _fail(where, "obrázek nejde přečíst (podporované jsou PNG a JPEG)") from e
    fmt = (img.format or "").lower().replace("jpeg", "jpg")
    if "formats" in v and fmt not in v["formats"]:
        raise _fail(where, f"formát {fmt} není povolený")
    if img.width < v.get("min_width_px", 0):
        raise _fail(where, f"užší než {v['min_width_px']} px")
    return img


def _flatten(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    base = Image.new("RGBA", rgba.size, "white")
    base.alpha_composite(rgba)
    return base.convert("RGB")


def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    _flatten(img).save(out, "JPEG", quality=90)
    return out.getvalue()


def _png(img: Image.Image, size: int) -> bytes:
    """Logo vepsané do čtverce na bílém pozadí."""
    flat = _flatten(img)
    flat.thumbnail((size, size))
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(flat, ((size - flat.width) // 2, (size - flat.height) // 2))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def _tokens(item: dict[str, Any], value: Any, default_css: str, where: str) -> bytes:
    css = default_css
    for field in item["fields"]:
        color = (value or {}).get(field["id"], field.get("default"))
        if not isinstance(color, str) or not HEX.match(color):
            raise _fail(f"{where}.{field['id']}", "očekávám barvu #rrggbb")
        css, n = re.subn(rf"({re.escape(field['token'])}\s*:\s*)[^;]+;", rf"\g<1>{color};", css, count=1)
        if n != 1:
            raise PermanentError(f"Výchozí tokens.css nemá token {field['token']}")
    return css.encode()


def secret_input_ids(manifest: Manifest) -> list[str]:
    return [i["id"] for i in manifest.raw.get("inputs") or [] if str(i.get("target", "")).startswith("secret:")]


def secrets(manifest: Manifest, inputs: dict[str, Any]) -> dict[str, str]:
    """Hodnoty Inputů s cílem `secret:<ENV>` jako proměnné prostředí (Fly secrets). Neprázdné, hodnoty se převedou na text."""
    out: dict[str, str] = {}
    for item in manifest.raw.get("inputs") or []:
        target = str(item.get("target", ""))
        if not target.startswith("secret:"):
            continue
        value = inputs.get(item["id"], item.get("default"))
        if _empty(value):
            if item.get("required"):
                raise _fail(item["id"], "chybí povinná hodnota")
            continue
        _check_scalar(item, value, item["id"])
        out[target.removeprefix("secret:")] = str(value)
    return out


def build(manifest: Manifest, inputs: dict[str, Any], default_tokens_css: str = "") -> tuple[dict[str, Any], dict[str, bytes]]:
    """Vrátí (payload, soubory brandingu podle názvu souboru). `inputs`: id Inputu → hodnota (obrázek jako bytes)."""
    payload: dict[str, Any] = {}
    files: dict[str, bytes] = {}
    json_files: dict[str, dict[str, Any]] = {}
    known = {i["id"] for i in manifest.raw.get("inputs") or []}
    if unknown := set(inputs) - known:
        raise PermanentError(f"Neznámé Inputy: {', '.join(sorted(unknown))}")

    for item in manifest.raw.get("inputs") or []:
        where = item["id"]
        kind = item["type"]
        value = inputs.get(where, item.get("default"))
        if kind != "color_tokens" and _empty(value):
            if item.get("required"):
                raise _fail(where, "chybí povinná hodnota")
            continue
        target: str = item["target"]
        if kind == "table":
            value = _table(item, value, where)
        elif kind == "image":
            img = _image(item, value, where)
            files[LOGO] = _jpeg(img)
            for name, size in ICONS.items():
                files[name] = _png(img, size)
            continue
        elif kind == "color_tokens":
            files[TOKENS] = _tokens(item, value, default_tokens_css, where)
            continue
        else:
            _check_scalar(item, value, where)

        if target.startswith("secret:"):
            continue  # jde do Fly secrets, viz secrets()
        if target.startswith("branding:"):
            name, _, key = target.removeprefix("branding:").partition("#")
            if not key:
                raise PermanentError(f"Input {where}: branding cíl {name} bez #klíče nejde pro typ {kind}")
            json_files.setdefault(name, {})[key] = value
        else:
            _set_path(payload, target.removeprefix("payload:"), value)

    for path, value in (manifest.raw.get("payload_constants") or {}).items():
        _set_path(payload, path, value)
    for name, data in json_files.items():
        files[name] = json.dumps(data, ensure_ascii=False).encode()
    return payload, files
