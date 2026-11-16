"""Epic OAuth: login URL, code extraction, token exchange (SPEC.md §6.2).

Pure functions wherever possible — the clipboard parser and URL builder are
unit-tested without a network or a Blender install.
"""

from __future__ import annotations

import base64
import re
import time
import urllib.parse
from typing import Optional

from ..api import endpoints as ep
from ..core import errors, log
from .models import TokenBundle, parse_oauth_response

log = log.get("oauth")

TIMEOUT = (10, 30)

# Epic has no friendly consent page for the launcher client: after sign-in the
# browser lands on a JSON blob
#   {"warning": "...", "redirectUrl": "...?code=<code>", "authorizationCode": "<code>"}
# and the code has to be copied from it. Community tools also accept the bare
# code and the redirected URL. Anything with dots or slashes is a JWT or a path
# — not a code.
_CODE_QUOTED = (
    re.compile(r'"authorizationCode"\s*:\s*"([A-Za-z0-9_-]{8,128})"'),
    re.compile(r'"code"\s*:\s*"([A-Za-z0-9_-]{8,128})"'),
)
_CODE_QUERY = re.compile(r"[?&]code=([A-Za-z0-9_-]{8,128})")
_CODE_BARE = re.compile(r"^[A-Za-z0-9]{16,64}$")

SUCCESS_EXCHANGE = ("authorization_code", "exchange_code")


def build_login_url(client_id: str = ep.CLIENT_LAUNCHER[0], redirect_uri: str | None = None) -> str:
    """Epic login URL.

    Default (what the sign-in operator opens): Epic's `/id/api/redirect` page,
    which signs in and then renders the authorization code as JSON. That is the
    URL community launcher clients use.

    `redirect_uri` is kept for experiments only: pointing Epic at
    `https://localhost/launcher/authorized` directly does **not** work. The
    account portal moved to `accounts.epicgames.com`, drops that unregistered
    redirect, and leaves a signed-in user on their account settings page (seen
    live 2026-11-16). Automatic capture therefore rides on the `redirectUrl`
    link inside the JSON page, not on this parameter.
    """
    if redirect_uri:
        return f"{ep.LOGIN_PAGE}?redirectUrl={urllib.parse.quote(redirect_uri, safe='')}"
    redirect = f"{ep.CODE_REDIRECT}?clientId={client_id}&responseType=code"
    return f"{ep.LOGIN_PAGE}?redirectUrl={urllib.parse.quote(redirect, safe='')}"


def extract_code(text: str) -> Optional[str]:
    """Pull an authorization code out of whatever the user copied.

    Returns None rather than guessing: a false positive would burn a token
    request and confuse the user with an opaque error.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > 20_000:
        return None

    for pattern in _CODE_QUOTED:
        match = pattern.search(stripped)
        if match:
            return match.group(1)

    match = _CODE_QUERY.search(stripped)
    if match:
        return match.group(1)

    if _CODE_BARE.match(stripped):
        return stripped
    return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _requests():
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - Blender always ships it
        raise errors.NetworkError("Python 'requests' is unavailable in this build") from exc
    return requests


def _basic(client_id: str, secret: str) -> str:
    raw = f"{client_id}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _token_request(form: dict, client_id: str, secret: str) -> TokenBundle:
    requests = _requests()
    headers = {
        "Authorization": _basic(client_id, secret),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "FabBridge/0.1 (Blender)",
    }
    try:
        resp = requests.post(
            ep.OAUTH_TOKEN, data=form, headers=headers, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        raise errors.NetworkError(str(exc)) from exc

    if resp.status_code == 401:
        # Bad/expired code is a user action, not an expired session.
        raise errors.AuthRequired(_body(resp, "authorization code rejected"))
    if resp.status_code == 400:
        # The only variable in this request is the credential (code or refresh
        # token), so a 400 is always "sign in again", never "the API changed".
        detail = _body(resp, "token request rejected")
        hint = f"{detail} {getattr(resp, 'text', '') or ''}".lower()
        err = errors.AuthRequired(detail)
        if "authorization_code" in hint or "invalid_grant" in hint or "expired" in hint:
            err.user_message = "That sign-in code was already used or has expired. Sign in again."
        else:
            err.user_message = "Epic rejected the sign-in request. Sign in again."
        raise err
    if resp.status_code >= 400:
        raise errors.from_status(resp.status_code, body=_body(resp))

    try:
        payload = resp.json()
    except ValueError as exc:
        raise errors.ApiChanged("token endpoint returned no JSON") from exc

    try:
        bundle = parse_oauth_response(payload, client_id)
    except (KeyError, TypeError, ValueError) as exc:
        # Never let a raw parse error reach the UI as "ValueError".
        raise errors.ApiChanged(f"unexpected token payload: {type(exc).__name__}") from exc
    log.info("token granted for %s (expires in %ds)", bundle.display_name or "?",
             int(bundle.expires_at - time.time()))
    return bundle


def _body(resp, fallback: str = "") -> str:
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or fallback)[:300]
    if isinstance(data, dict):
        return str(data.get("errorCode") or data.get("errorMessage") or data)[:300]
    return fallback


def exchange_code(code: str, *, client_id: str | None = None,
                  secret: str | None = None) -> TokenBundle:
    client_id = client_id or ep.CLIENT_LAUNCHER[0]
    secret = secret or ep.CLIENT_LAUNCHER[1]
    return _token_request(
        {"grant_type": "authorization_code", "code": code, "token_type": "eg1"},
        client_id,
        secret,
    )


def refresh(bundle: TokenBundle) -> TokenBundle:
    secret = ep.CLIENT_LAUNCHER[1] if bundle.client_id == ep.CLIENT_LAUNCHER[0] else None
    if secret is None:
        raise errors.AuthExpired("no client secret for this session")
    return _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": bundle.refresh_token,
            "token_type": "eg1",
        },
        bundle.client_id,
        secret,
    )


def kill_session(access_token: str) -> None:
    """Best-effort remote sign-out. Never fails the local sign-out."""
    requests = _requests()
    try:
        requests.delete(
            ep.OAUTH_KILL.format(token=urllib.parse.quote(access_token, safe="")),
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately swallowed
        log.debug("session kill failed: %s", type(exc).__name__)


def describe(bundle: TokenBundle | None) -> str:
    if not bundle:
        return "Signed out"
    return f"Signed in as {bundle.display_name or bundle.account_id or 'unknown'}"
