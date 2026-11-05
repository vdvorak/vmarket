"""Cloudflare: DNS záznamy a R2 (bucket + token omezený na jeden bucket).

DNS záznamy jsou jen CNAME bez proxy (Fly si na hostname vystaví vlastní certifikát). R2 přístupy pro Litestream jsou S3
klíče odvozené z API tokenu: Access Key ID = id tokenu, Secret = SHA-256 hodnoty tokenu (docs: R2 → S3 API tokens).

Token se vrací jen při vytvoření. Opakované `ensure_backup_bucket` (po pádu mezi vytvořením a uložením) proto starý token
téhož jména smaže a vydá nový. Provisioner volá krok jen dokud není hotový, takže běžící Instance o klíče nepřijde."""

import hashlib

import httpx2

from vmarket.adapters.http import Api
from vmarket.provisioning.ports import AdapterError, PermanentError, Secrets

API = "https://api.cloudflare.com/client/v4"
BUCKET_WRITE_GROUP = "Workers R2 Storage Bucket Item Write"


def _result(payload: dict[str, object]) -> object:
    if payload.get("success") is False:
        raise PermanentError(f"Cloudflare: {payload.get('errors')}")
    return payload.get("result")


class CloudflareDns:
    """Potřebuje token s oprávněním Zone → DNS → Edit pro zónu."""

    def __init__(self, token: str, zone_id: str, *, transport: httpx2.BaseTransport | None = None) -> None:
        self.api = Api(API, token, transport=transport)
        self.zone = zone_id

    def _find(self, name: str) -> list[dict[str, str]]:
        found = _result(self.api.json("GET", f"/zones/{self.zone}/dns_records", params={"name": name}))
        return list(found) if isinstance(found, list) else []

    def ensure_cname(self, name: str, target: str) -> None:
        body = {"type": "CNAME", "name": name, "content": target, "proxied": False, "ttl": 1}
        existing = self._find(name)
        if not existing:
            _result(self.api.json("POST", f"/zones/{self.zone}/dns_records", json=body))
            return
        record = existing[0]
        if record["type"] != "CNAME":
            raise PermanentError(f"DNS: {name} už existuje jako {record['type']}, CNAME se nezaloží")
        if record["content"] != target:
            _result(self.api.json("PUT", f"/zones/{self.zone}/dns_records/{record['id']}", json=body))

    def delete_cname(self, name: str) -> None:
        for record in self._find(name):
            if record["type"] == "CNAME":
                self.api.json("DELETE", f"/zones/{self.zone}/dns_records/{record['id']}")


class R2Store:
    """Potřebuje token s oprávněním Account → Workers R2 Storage → Edit a Account → API Tokens → Edit."""

    def __init__(
        self, token: str, account_id: str, *, jurisdiction: str = "default", transport: httpx2.BaseTransport | None = None
    ) -> None:
        self.api = Api(API, token, transport=transport)
        self.account = account_id
        self.jurisdiction = jurisdiction
        self._group: str | None = None

    @property
    def endpoint(self) -> str:
        host = "" if self.jurisdiction == "default" else f"{self.jurisdiction}."
        return f"https://{self.account}.{host}r2.cloudflarestorage.com"

    def _permission_group(self) -> str:
        if self._group is None:
            groups = _result(self.api.json("GET", f"/accounts/{self.account}/tokens/permission_groups"))
            match = [g["id"] for g in groups if g["name"] == BUCKET_WRITE_GROUP] if isinstance(groups, list) else []
            if not match:
                raise PermanentError(f"Cloudflare nezná oprávnění {BUCKET_WRITE_GROUP!r}")
            self._group = str(match[0])
        return self._group

    def ensure_backup_bucket(self, name: str) -> Secrets:
        headers = {} if self.jurisdiction == "default" else {"cf-r2-jurisdiction": self.jurisdiction}
        r = self.api.request("POST", f"/accounts/{self.account}/r2/buckets", json={"name": name}, headers=headers, allow={409})
        if r.status_code == 409:
            pass  # bucket už existuje (opakovaný krok)
        token_name = f"vmarket-{name}"
        for t in _result(self.api.json("GET", f"/accounts/{self.account}/tokens")) or []:  # type: ignore[attr-defined]
            if t["name"] == token_name:
                self.api.request("DELETE", f"/accounts/{self.account}/tokens/{t['id']}", allow={404})
        resource = f"com.cloudflare.edge.r2.bucket.{self.account}_{self.jurisdiction}_{name}"
        created = _result(
            self.api.json(
                "POST",
                f"/accounts/{self.account}/tokens",
                json={
                    "name": token_name,
                    "policies": [
                        {"effect": "allow", "resources": {resource: "*"}, "permission_groups": [{"id": self._permission_group()}]}
                    ],
                },
            )
        )
        if not isinstance(created, dict) or "value" not in created:
            raise AdapterError("Cloudflare nevrátil hodnotu tokenu")
        return Secrets(
            {
                "LITESTREAM_REPLICA_URL": f"s3://{name}/db",
                "LITESTREAM_S3_ENDPOINT": self.endpoint,
                "LITESTREAM_S3_REGION": "auto",
                "LITESTREAM_S3_FORCE_PATH_STYLE": "true",
                "LITESTREAM_ACCESS_KEY_ID": str(created["id"]),
                "LITESTREAM_SECRET_ACCESS_KEY": hashlib.sha256(str(created["value"]).encode()).hexdigest(),
            }
        )
