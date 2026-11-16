"""Catch Epic's launcher redirect on `https://localhost:443` (SPEC.md §6.3).

The Epic Games Launcher does not copy codes: the login page redirects the
browser to `https://localhost/launcher/authorized?code=…`, where a small local
HTTPS server picks the code up. That is the closest thing to a one-click login
Epic offers a third-party app, and it is what `FABBRIDGE_OT_login` prefers.

Costs, stated plainly in the UI: the certificate is self-signed, so the browser
shows a "not private" interstitial the first time (one click, then the code is
captured); and port 443 is unavailable if the real launcher is running. Both
cases fall back to the clipboard flow.

Nothing here touches the token endpoint — `auth.oauth` still does the exchange.
"""

from __future__ import annotations

import os
import ssl
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from ..core import log

log = log.get("loopback")

HOST = "127.0.0.1"
PORT = 443
PATH = "/launcher/authorized"
CERT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "localhost.crt")
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "localhost.key")

_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Fab Bridge</title>
<style>
  html,body{{height:100%;margin:0}}
  body{{display:flex;align-items:center;justify-content:center;
        font:16px/1.5 system-ui,sans-serif;background:#1d1d1d;color:#eee}}
  main{{text-align:center;padding:2rem}}
  .tick{{font-size:3rem;color:#5bbd72}}
  p{{color:#aaa}}
</style>
<main>
  <div class="tick">&#10003;</div>
  <h1>{title}</h1>
  <p>{detail}</p>
</main>
"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FabBridge"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != PATH:
            self._send(404, "Not found", "This is Fab Bridge's sign-in catcher.")
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or params.get("authorizationCode") or [""])[0]
        listener: Listener = self.server.listener  # type: ignore[attr-defined]
        if code:
            listener.code = code
            self._send(200, "Signed in to Fab Bridge", "You can close this tab.")
        else:
            listener.error = "Epic returned no code"
            self._send(400, "Sign-in failed", "No authorization code was returned.")

    def do_HEAD(self):  # noqa: N802
        self._send(200, "", "", body=False)

    def _send(self, status: int, title: str, detail: str, *, body: bool = True) -> None:
        payload = _PAGE.format(title=title, detail=detail).encode("utf-8") if body else b""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, fmt, *args):  # noqa: A003 - silence the default stderr spam
        log.debug("loopback: " + fmt, *args)


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR lets a second process hijack the port silently, so
    # only ask for it where it means "skip TIME_WAIT" (POSIX).
    allow_reuse_address = sys.platform != "win32"


class Listener:
    """One-shot HTTPS listener. `start()` never raises; it returns False."""

    def __init__(self, port: int = PORT):
        self.port = port
        self.code = ""
        self.error = ""
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        if self._server is not None:
            return True
        if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
            log.info("no loopback certificate bundled; using the clipboard")
            return False

        try:
            server = _Server((HOST, self.port), _Handler)
        except OSError as exc:
            # Port 443 belongs to the Epic launcher (or something else).
            log.info("cannot listen on %s:%s (%s); using the clipboard", HOST, self.port, exc)
            return False

        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(CERT_FILE, KEY_FILE)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            log.warning("loopback TLS setup failed: %s", type(exc).__name__)
            server.server_close()
            return False

        server.listener = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="fab-loopback", daemon=True)
        self._thread.start()
        log.info("listening on https://localhost:%s%s", self.port, PATH)
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001 - shutdown must never break sign-out
            pass
        log.info("loopback listener stopped")


def redirect_url(port: int = PORT) -> str:
    """The launcher client's registered redirect, pointed at our port."""
    if port == 443:
        return f"https://localhost{PATH}"
    return f"https://localhost:{port}{PATH}"
