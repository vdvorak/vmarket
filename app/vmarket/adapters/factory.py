"""Sestavení skutečných adaptérů z proměnných prostředí. Chybějící proměnné vyjmenuje všechny najednou."""

import os
from collections.abc import Mapping

from vmarket.adapters.cloudflare import CloudflareDns, R2Store
from vmarket.adapters.fly import FlyApi
from vmarket.adapters.probe import HttpProbe
from vmarket.adapters.resend import ResendMail
from vmarket.provisioning.ports import Adapters

REQUIRED = {
    "FLY_API_TOKEN": "token s právem zakládat app v organizaci Instancí",
    "FLY_ORG_SLUG": "slug Fly organizace pro Instance (ADR 0001)",
    "CLOUDFLARE_DNS_TOKEN": "Zone → DNS → Edit pro zónu Instancí",
    "CLOUDFLARE_ZONE_ID": "id zóny s doménou Instancí",
    "CLOUDFLARE_R2_TOKEN": "Account → R2 Storage → Edit a API Tokens → Edit",
    "CLOUDFLARE_ACCOUNT_ID": "id účtu Cloudflare",
    "RESEND_API_KEY": "plný přístup (zakládá klíče pro Instance)",
    "RESEND_DOMAIN": "ověřená odesílací doména, např. mail.vitekdvorak.cz",
}


class ConfigError(Exception):
    pass


def from_env(env: Mapping[str, str] | None = None) -> Adapters:
    e = os.environ if env is None else env
    if missing := [f"{k} ({v})" for k, v in REQUIRED.items() if not e.get(k)]:
        raise ConfigError("Chybí proměnné prostředí:\n  " + "\n  ".join(missing))
    return Adapters(
        fly=FlyApi(e["FLY_API_TOKEN"], e["FLY_ORG_SLUG"]),
        dns=CloudflareDns(e["CLOUDFLARE_DNS_TOKEN"], e["CLOUDFLARE_ZONE_ID"]),
        store=R2Store(e["CLOUDFLARE_R2_TOKEN"], e["CLOUDFLARE_ACCOUNT_ID"], jurisdiction=e.get("CLOUDFLARE_R2_JURISDICTION", "default")),
        mail=ResendMail(e["RESEND_API_KEY"], e["RESEND_DOMAIN"], e.get("RESEND_DOMAIN_ID") or None),
        probe=HttpProbe(),
    )
