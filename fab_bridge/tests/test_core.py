"""Headless tests for the parts of the addon that do not need Blender.

    python -m unittest discover -s fab_bridge/tests -t .
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.api import endpoints as ep  # noqa: E402
from fab_bridge.auth import models, oauth, store  # noqa: E402
from fab_bridge.core import errors, log  # noqa: E402


class CodeParsingTests(unittest.TestCase):
    def test_json_authorization_code(self):
        blob = json.dumps({"redirectUrl": "https://x", "authorizationCode": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"})
        self.assertEqual(oauth.extract_code(blob), "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")

    def test_the_epic_json_page_as_shown_in_a_browser(self):
        # Verbatim shape of what epicgames.com/id/api/redirect renders after
        # login (2026-11-16). Users copy this with Ctrl+A / the page's Copy.
        page = """{
          "warning": "Do not share this code with any 3rd party service. It allows full access to your Epic account.",
          "redirectUrl": "https://localhost/launcher/authorized?code=c9b5185aa90e4e54830b4169116b9142",
          "authorizationCode": "c9b5185aa90e4e54830b4169116b9142",
          "exchangeCode": null,
          "sid": null
        }"""
        self.assertEqual(oauth.extract_code(page), "c9b5185aa90e4e54830b4169116b9142")
        # The redirect URL on its own is enough too.
        self.assertEqual(
            oauth.extract_code("https://localhost/launcher/authorized?code=c9b5185aa90e4e54830b4169116b9142"),
            "c9b5185aa90e4e54830b4169116b9142",
        )

    def test_json_code_key(self):
        self.assertEqual(oauth.extract_code('{"code":"deadbeefdeadbeef"}'), "deadbeefdeadbeef")

    def test_bare_code(self):
        code = "0123456789abcdef0123456789abcdef"
        self.assertEqual(oauth.extract_code(f"  {code}\n"), code)

    def test_query_code(self):
        url = "https://www.epicgames.com/id/api/redirect?code=0123456789abcdef"
        self.assertEqual(oauth.extract_code(url), "0123456789abcdef")

    def test_rejects_junk(self):
        for junk in ("", "   ", "hello world", "a.b.c", "/usr/bin/thing",
                     "x" * 30_000, "short"):
            self.assertIsNone(oauth.extract_code(junk), junk[:20])

    def test_rejects_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertIsNone(oauth.extract_code(jwt))


class LoginUrlTests(unittest.TestCase):
    def test_contains_quoted_redirect(self):
        url = oauth.build_login_url()
        self.assertTrue(url.startswith(ep.LOGIN_PAGE + "?redirectUrl="))
        self.assertIn(ep.CLIENT_LAUNCHER[0], url)
        # The redirect must survive as a single encoded parameter.
        self.assertNotIn("&responseType=code", url)

    def test_custom_client(self):
        self.assertIn(ep.CLIENT_DESKTOP[0], oauth.build_login_url(ep.CLIENT_DESKTOP[0]))


class TokenBundleTests(unittest.TestCase):
    def make(self, **kw):
        base = dict(
            access_token="a", refresh_token="r", expires_at=time.time() + 7200,
            refresh_expires_at=time.time() + 28800, account_id="acc",
            display_name="Name", client_id=ep.CLIENT_LAUNCHER[0],
        )
        base.update(kw)
        return models.TokenBundle(**base)

    def test_fresh_token_needs_no_refresh(self):
        self.assertFalse(self.make().needs_refresh())

    def test_refresh_within_margin(self):
        self.assertTrue(self.make(expires_at=time.time() + 60).needs_refresh())

    def test_cannot_refresh_after_window(self):
        bundle = self.make(refresh_expires_at=time.time() - 1)
        self.assertFalse(bundle.can_refresh())

    def test_round_trip(self):
        bundle = self.make()
        self.assertEqual(models.TokenBundle.from_dict(bundle.to_dict()), bundle)

    def test_parse_expires_in(self):
        now = 1_000_000.0
        bundle = models.parse_oauth_response(
            {"access_token": "a", "refresh_token": "r", "expires_in": 7200,
             "refresh_expires_in": 28800, "account_id": "acc", "displayName": "Zed"},
            ep.CLIENT_LAUNCHER[0], now=now,
        )
        self.assertAlmostEqual(bundle.expires_at, now + 7200)
        self.assertAlmostEqual(bundle.refresh_expires_at, now + 28800)
        self.assertEqual(bundle.display_name, "Zed")

    def test_parse_the_real_epic_payload(self):
        """Shape measured on a live `authorization_code` grant (2026-11-16).

        `refresh_expires` is in seconds while `refresh_expires_at` is an ISO
        string — feeding that string to `float()` is what broke sign-in.
        """
        now = 1_763_300_000.0
        payload = {
            "access_token": "at",
            "expires_in": 7200,
            "expires_at": "2026-11-16T17:14:05.891Z",
            "token_type": "bearer",
            "refresh_token": "rt",
            "refresh_expires": 28800,
            "refresh_expires_at": "2026-11-17T15:14:05.891Z",
            "account_id": "acc",
            "client_id": ep.CLIENT_LAUNCHER[0],
            "internal_client": True,
            "client_service": "launcher",
            "displayName": "Test User",
            "app": "launcher",
            "country": "US",
        }
        bundle = models.parse_oauth_response(payload, ep.CLIENT_LAUNCHER[0], now=now)
        self.assertAlmostEqual(bundle.expires_at, now + 7200)
        self.assertAlmostEqual(bundle.refresh_expires_at, now + 28800)
        self.assertEqual(bundle.display_name, "Test User")
        self.assertEqual(bundle.refresh_token, "rt")

    def test_iso_absolute_expiry_without_seconds_field(self):
        from datetime import datetime

        now = 1_763_300_000.0
        bundle = models.parse_oauth_response(
            {"access_token": "at", "expires_at": "2026-11-16T17:14:05.891Z",
             "refresh_expires_at": "2026-11-17T15:14:05.891Z"},
            ep.CLIENT_LAUNCHER[0], now=now,
        )
        expect = datetime.fromisoformat("2026-11-16T17:14:05.891+00:00").timestamp()
        expect_refresh = datetime.fromisoformat("2026-11-17T15:14:05.891+00:00").timestamp()
        self.assertAlmostEqual(bundle.expires_at, expect, places=3)
        self.assertAlmostEqual(bundle.refresh_expires_at, expect_refresh, places=3)
        self.assertGreater(bundle.expires_at, now, "must be an epoch, not a duration")

    def test_missing_access_token_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            models.parse_oauth_response({"expires_in": 7200}, "cid")


class _FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload
        self._payload = payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("no json")
        return self._payload


class _FakeRequests:
    RequestException = Exception

    def __init__(self, response):
        self.response = response

    def post(self, *args, **kwargs):
        return self.response


class TokenExchangeErrorTests(unittest.TestCase):
    def _exchange(self, response):
        with unittest.mock.patch.object(oauth, "_requests", lambda: _FakeRequests(response)):
            return oauth.exchange_code("c0ffee")

    def test_used_code_is_a_sign_in_problem_not_an_api_change(self):
        body = {
            "errorCode": "errors.com.epicgames.account.oauth.authorization_code_not_found",
            "errorMessage": "Sorry the authorization code is invalid.",
        }
        with self.assertRaises(errors.AuthRequired) as caught:
            self._exchange(_FakeResponse(400, body))
        self.assertIn("already used", caught.exception.user_message)
        self.assertNotIsInstance(caught.exception, errors.ApiChanged)

    def test_unexpected_token_payload_is_reported_as_api_changed(self):
        with self.assertRaises(errors.ApiChanged):
            self._exchange(_FakeResponse(200, {"token_type": "bearer"}))

    def test_a_real_payload_becomes_a_bundle(self):
        now = time.time()
        bundle = self._exchange(
            _FakeResponse(
                200,
                {
                    "access_token": "at",
                    "expires_in": 7200,
                    "expires_at": "2030-01-01T00:00:00.000Z",
                    "refresh_token": "rt",
                    "refresh_expires": 28800,
                    "account_id": "acc",
                    "displayName": "Zed",
                },
            )
        )
        self.assertEqual(bundle.access_token, "at")
        self.assertAlmostEqual(bundle.expires_at, now + 7200, delta=5)
        self.assertAlmostEqual(bundle.refresh_expires_at, now + 28800, delta=5)
        self.assertEqual(bundle.display_name, "Zed")


class ErrorMappingTests(unittest.TestCase):
    def test_429_carries_retry_after(self):
        err = errors.from_status(429, retry_after=120)
        self.assertIsInstance(err, errors.RateLimited)
        self.assertIn("120", err.user_message)

    def test_401_is_expired(self):
        self.assertIsInstance(errors.from_status(401), errors.AuthExpired)

    def test_5xx_is_network(self):
        self.assertIsInstance(errors.from_status(503), errors.NetworkError)

    def test_unknown_is_api_changed(self):
        self.assertIsInstance(errors.from_status(418), errors.ApiChanged)


class RedactionTests(unittest.TestCase):
    def test_secrets_are_stripped(self):
        record = logging.LogRecord(
            "fab_bridge", logging.INFO, __file__, 1,
            "GET https://cdn/x.zip?f_token=SECRET&cf_token=ALSO access_token=TOK code=ABC", (), None,
        )
        log.RedactFilter().filter(record)
        text = record.getMessage()
        for secret in ("SECRET", "ALSO", "TOK", "ABC"):
            self.assertNotIn(secret, text)
        self.assertIn("<redacted>", text)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-test-")
        self._orig = store.config_dir
        store.config_dir = lambda: self.tmp

    def tearDown(self):
        store.config_dir = self._orig
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def bundle(self, client_id=ep.CLIENT_LAUNCHER[0]):
        return models.TokenBundle(
            access_token="access", refresh_token="refresh",
            expires_at=time.time() + 100, refresh_expires_at=time.time() + 200,
            account_id="acc", display_name="Tester", client_id=client_id,
        )

    def test_save_load_clear(self):
        try:
            store.save(self.bundle())
        except OSError as exc:
            self.skipTest(f"platform keystore unavailable: {exc}")
        loaded = store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.display_name, "Tester")
        store.clear()
        self.assertIsNone(store.load())

    def test_client_change_discards_session(self):
        try:
            store.save(self.bundle(client_id="old-client"))
        except OSError as exc:
            self.skipTest(f"platform keystore unavailable: {exc}")
        self.assertIsNone(store.load(expected_client_id=ep.CLIENT_LAUNCHER[0]))

    def test_corrupt_file_is_discarded(self):
        path = os.path.join(self.tmp, store.FILENAME)
        with open(path, "wb") as fh:
            fh.write(b"\x00not a session")
        self.assertIsNone(store.load())
        self.assertFalse(os.path.exists(path))


class EndpointSanityTests(unittest.TestCase):
    def test_sort_values_are_the_verified_five(self):
        self.assertEqual(
            set(ep.SORT_VALUES),
            {"-relevance", "-publishedAt", "publishedAt", "-firstPublishedAt",
             "-ratings.averageRating"},
        )

    def test_importable_filter_subset_of_importable(self):
        self.assertTrue(set(ep.IMPORTABLE_FILTER) <= set(ep.IMPORTABLE_FORMATS))

    def test_page_size_is_the_server_cap(self):
        self.assertEqual(ep.PAGE_SIZE, 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
