"""Tenký klient nad httpx2: Bearer token, JSON, převod chyb na AdapterError/PermanentError.

5xx, 429 a síťové chyby jsou dočasné (AdapterError → provisioner zkusí znovu). 4xx (kromě 429) je chyba požadavku
nebo oprávnění; opakování nepomůže, proto PermanentError. 404 vrací volající sám přes `allow`."""

from collections.abc import Collection
from typing import Any

import httpx2

from vmarket.provisioning.ports import AdapterError, PermanentError


class Api:
    def __init__(self, base_url: str, token: str, *, transport: httpx2.BaseTransport | None = None, timeout: float = 30.0) -> None:
        self.client = httpx2.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, transport=transport, timeout=timeout)

    def request(self, method: str, path: str, *, allow: Collection[int] = (), **kwargs: Any) -> httpx2.Response:
        try:
            r = self.client.request(method, path, **kwargs)
        except httpx2.TransportError as e:
            raise AdapterError(f"{method} {path}: {e}") from e
        if r.is_success or r.status_code in allow:
            return r
        detail = f"{method} {path} → {r.status_code}: {r.text[:300]}"
        if r.status_code >= 500 or r.status_code == 429:
            raise AdapterError(detail)
        raise PermanentError(detail)

    def json(self, method: str, path: str, *, allow: Collection[int] = (), **kwargs: Any) -> Any:
        r = self.request(method, path, allow=allow, **kwargs)
        return r.json() if r.content else None
