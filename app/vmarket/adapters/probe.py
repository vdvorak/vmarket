"""Externí kontrola Instance (health check z internetu)."""

import httpx2


class HttpProbe:
    def __init__(self, *, transport: httpx2.BaseTransport | None = None, timeout: float = 5.0) -> None:
        self.client = httpx2.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def get(self, url: str) -> int | None:
        try:
            return self.client.get(url).status_code
        except httpx2.HTTPError:
            return None
