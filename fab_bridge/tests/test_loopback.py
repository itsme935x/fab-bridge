"""The localhost redirect catcher (SPEC.md §6.3).

Runs a real TLS server on an ephemeral port and talks to it with an unverified
context — the certificate is self-signed on purpose, which is exactly what the
browser complains about once.
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.auth import loopback  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.listener = loopback.Listener(port=free_port())
        self.assertTrue(self.listener.start(), "the bundled certificate must load")
        self.addCleanup(self.listener.stop)

    def get(self, path):
        context = ssl._create_unverified_context()
        conn = http.client.HTTPSConnection("127.0.0.1", self.listener.port, context=context, timeout=10)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        conn.close()
        return response.status, body

    def test_captures_the_code_from_the_launcher_redirect(self):
        status, body = self.get("/launcher/authorized?code=c9b5185aa90e4e54830b4169116b9142")
        self.assertEqual(status, 200)
        self.assertIn("Signed in", body)
        self.assertEqual(self.listener.code, "c9b5185aa90e4e54830b4169116b9142")

    def test_accepts_the_authorizationcode_key_too(self):
        status, _ = self.get("/launcher/authorized?authorizationCode=deadbeefdeadbeef")
        self.assertEqual(status, 200)
        self.assertEqual(self.listener.code, "deadbeefdeadbeef")

    def test_other_paths_are_not_mistaken_for_a_callback(self):
        status, _ = self.get("/favicon.ico")
        self.assertEqual(status, 404)
        self.assertFalse(self.listener.code)

    def test_missing_code_is_reported(self):
        status, _ = self.get("/launcher/authorized")
        self.assertEqual(status, 400)
        self.assertTrue(self.listener.error)
        self.assertFalse(self.listener.code)

    def test_redirect_url_matches_the_launcher_client_registration(self):
        self.assertEqual(loopback.redirect_url(), "https://localhost/launcher/authorized")
        self.assertEqual(
            loopback.redirect_url(8443), "https://localhost:8443/launcher/authorized"
        )

    def test_a_second_listener_on_the_same_port_fails_instead_of_raising(self):
        other = loopback.Listener(port=self.listener.port)
        self.assertFalse(other.start())


if __name__ == "__main__":
    unittest.main(verbosity=2)
