"""
Tiny HTTP health endpoint for hosts that require an open port
(Render Web Service, Railway, generic PaaS health checks).

Exposes:
  GET /          → 200 "ictbot scanner running"
  GET /health    → 200 with the heartbeat age in seconds, or 503 if stale
  GET /healthz   → alias of /health (k8s-style naming)

Threaded so a slow pinger client can't block other requests. The server
runs on a background daemon thread so it dies with the scanner process —
nothing here owns shutdown.

Why stdlib instead of FastAPI/Flask:
  - No new dependency. The scanner image stays small.
  - The endpoint exists to keep the host's idle-timer happy and surface a
    binary "alive vs dead" signal. Nothing here needs routing, validation,
    or JSON schemas.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ictbot.runtime import heartbeat

log = logging.getLogger("ictbot.runtime.health_server")

# Stale threshold for /health. The scanner sleeps 30 s between cycles, so
# a heartbeat older than 5× that means the loop is genuinely stuck.
DEFAULT_STALE_THRESHOLD_S = 150


class _HealthHandler(BaseHTTPRequestHandler):
    stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S

    def do_GET(self) -> None:  # noqa: N802 — http.server contract
        if self.path == "/" or self.path == "":
            self._reply(200, "ictbot scanner running\n")
            return
        if self.path in ("/health", "/healthz"):
            age = heartbeat.age_seconds()
            if age is None:
                # Scanner just started — no heartbeat yet. Treat as
                # warming up, not dead.
                self._reply(200, "starting (no heartbeat yet)\n")
                return
            if age > self.stale_threshold_s:
                self._reply(
                    503,
                    f"stale heartbeat: {age:.0f}s old (threshold {self.stale_threshold_s:.0f}s)\n",
                )
                return
            self._reply(200, f"ok heartbeat_age_s={age:.1f}\n")
            return
        self._reply(404, "not found\n")

    def _reply(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            # Pinger hung up before we wrote — not interesting.
            pass

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Quiet the default request-line spam; route to module logger
        # so it's filterable alongside scanner output.
        log.debug("[health] " + fmt, *args)


def start_health_server(
    port: int,
    *,
    bind: str = "0.0.0.0",
    stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S,
) -> ThreadingHTTPServer:
    """Spin up the /health server on a daemon thread and return it.

    Hosts like Render require the process to bind `$PORT` quickly after
    boot or they kill the service. Call this before the scanner's main
    loop so the port is live within seconds of startup.
    """
    handler_cls = type(
        "_BoundHealthHandler",
        (_HealthHandler,),
        {"stale_threshold_s": stale_threshold_s},
    )
    httpd = ThreadingHTTPServer((bind, port), handler_cls)
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="ictbot-health-server",
        daemon=True,
    )
    thread.start()
    log.info(
        "health server listening on %s:%d (stale threshold %.0fs)",
        bind,
        port,
        stale_threshold_s,
    )
    return httpd
