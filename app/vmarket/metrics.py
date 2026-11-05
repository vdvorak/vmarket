"""Metriky pro Prometheus na vlastním interním portu (METRICS_PORT, 0 = vypnuto); veřejná aplikace `/metrics` nemá."""

import logging
import time

from prometheus_client import Counter, Histogram, start_http_server
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

REQUESTS = Counter("vmarket_http_requests_total", "Počet požadavků.", ["method", "route", "status"])
LATENCY = Histogram("vmarket_http_request_duration_seconds", "Doba zpracování požadavku.", ["method", "route"])
NO_ROUTE = "other"
_started = False


def start_server(port: int) -> None:
    global _started
    if port and not _started:
        start_http_server(port)
        _started = True
        log.info("Metriky na portu %s.", port)


class Metrics:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()

        async def wrapped(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", NO_ROUTE)
            REQUESTS.labels(scope["method"], path, str(status)).inc()
            LATENCY.labels(scope["method"], path).observe(time.perf_counter() - started)
