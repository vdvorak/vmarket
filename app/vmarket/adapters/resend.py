"""Resend přes SMTP. Odesílací doména je jedna sdílená (např. mail.vitekdvorak.cz), ověřená jednou ručně při zakládání
platformy (DKIM/SPF v Cloudflare). Každá Instance dostane vlastní API klíč jen pro odesílání z této domény, takže jde
odpojit samostatně a jeho únik nedovolí víc než posílat z naší domény. SMTP: smtp.resend.com, uživatel `resend`, heslo = klíč.

Klíč se vrací jen při vytvoření; opakované volání starý klíč téhož jména smaže a vydá nový (stejně jako R2 token).
`domain_id` v těle API klíče je podle dokumentace Resendu volitelné omezení na doménu; naostro neověřeno."""

import httpx2

from vmarket.adapters.http import Api
from vmarket.provisioning.ports import AdapterError, Secrets

API = "https://api.resend.com"


class ResendMail:
    def __init__(self, token: str, domain: str, domain_id: str | None = None, *, transport: httpx2.BaseTransport | None = None) -> None:
        self.api = Api(API, token, transport=transport)
        self.domain = domain
        self.domain_id = domain_id

    def ensure_sender(self, slug: str) -> Secrets:
        key_name = f"vmarket-{slug}"
        for key in self.api.json("GET", "/api-keys").get("data", []):
            if key["name"] == key_name:
                self.api.request("DELETE", f"/api-keys/{key['id']}", allow={404})
        body: dict[str, str] = {"name": key_name, "permission": "sending_access"}
        if self.domain_id:
            body["domain_id"] = self.domain_id
        created = self.api.json("POST", "/api-keys", json=body)
        if not created or "token" not in created:
            raise AdapterError("Resend nevrátil hodnotu API klíče")
        return Secrets(
            {
                "SMTP_HOST": "smtp.resend.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "resend",
                "SMTP_PASSWORD": str(created["token"]),
                "MAIL_FROM": f"{slug}@{self.domain}",
            }
        )
