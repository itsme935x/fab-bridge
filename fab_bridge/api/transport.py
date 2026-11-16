"""HTTP transport for the Fab API (SPEC.md §5.1, §5.6).

One place that knows about retries, 429s, 401-refresh, and rate budgets.
Callers get parsed JSON or a typed error — never a raw `requests` object.
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Optional

from ..core import errors, log

log = log.get("transport")

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_ATTEMPTS = 3
BACKOFF = (0.5, 1.5, 4.0)


def _requests():
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise errors.NetworkError("'requests' is unavailable in this Blender build") from exc
    return requests


def user_agent() -> str:
    try:
        import bpy  # noqa: PLC0415

        blender = bpy.app.version_string
    except Exception:
        blender = "unknown"
    import sys

    return f"FabBridge/0.1 (Blender {blender}; {sys.platform})"


# ---------------------------------------------------------------------------
# rate budgets
# ---------------------------------------------------------------------------
class _Bucket:
    """Minimal token bucket. Cheap, and enough for a polite client."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.stamp = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / self.rate
        time.sleep(wait)


BUCKETS = {
    "browse": _Bucket(rate=2.0, capacity=4),
    "library": _Bucket(rate=1.0, capacity=2),
    "claim": _Bucket(rate=1 / 0.32, capacity=1),
    "download_info": _Bucket(rate=2.0, capacity=2),
    "download": _Bucket(rate=1.0, capacity=2),
    "media": _Bucket(rate=3.0, capacity=4),
}


def bucket_for(url: str) -> str:
    if "media.fab.com" in url:
        return "media"
    if "/library/" in url:
        return "library"
    if "add-to-library" in url:
        return "claim"
    if "download-info" in url:
        return "download_info"
    return "browse"


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
_session = None
_session_lock = threading.Lock()


def requests_module():
    """The `requests` module, with the addon's friendly ImportError."""
    return _requests()


def session():
    """Shared connection pool. Callers that stream add their own headers."""
    global _session
    with _session_lock:
        if _session is None:
            requests = _requests()
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": user_agent(),
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                }
            )
            _session = s
        return _session


def reset_session() -> None:
    global _session
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:  # noqa: BLE001
                pass
        _session = None


def _auth_header() -> Optional[str]:
    from ..auth import manager as auth  # noqa: PLC0415 - avoid import cycle

    if not auth.MANAGER.signed_in:
        return None
    try:
        return f"Bearer {auth.MANAGER.access_token()}"
    except errors.FabError:
        return None


def _retry_after(resp) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# core request
# ---------------------------------------------------------------------------
def request(
    method: str,
    url: str,
    *,
    params: Any = None,
    data: Any = None,
    json_body: Any = None,
    require_auth: bool = False,
    send_auth: bool = True,
    stream: bool = False,
    timeout: tuple[int, int] = (CONNECT_TIMEOUT, READ_TIMEOUT),
    bucket: Optional[str] = None,
):
    """Perform a request with retries and auth refresh. Returns the response."""
    requests = _requests()
    sess = session()
    bucket_name = bucket or bucket_for(url)
    if bucket_name in BUCKETS:
        BUCKETS[bucket_name].take()

    refreshed = False
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_ATTEMPTS):
        headers = {}
        # Credentials never leave the Fab API host (§Appendix C).
        token = _auth_header() if send_auth else None
        if token:
            headers["Authorization"] = token
        elif require_auth:
            raise errors.AuthRequired()

        try:
            resp = sess.request(
                method, url, params=params, data=data, json=json_body,
                headers=headers, timeout=timeout, stream=stream,
            )
        except requests.Timeout as exc:
            last_exc = exc
            log.debug("timeout %s %s", method, url.split("?")[0])
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(_jitter(attempt))
                continue
            raise errors.NetworkError("timed out") from exc
        except requests.RequestException as exc:
            last_exc = exc
            log.debug("connection error %s: %s", type(exc).__name__, exc)
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(_jitter(attempt))
                continue
            raise errors.NetworkError(str(exc)) from exc

        # --- 401: refresh once, then give up ------------------------------
        if resp.status_code == 401:
            resp.close()
            if refreshed:
                from ..auth import manager as auth  # noqa: PLC0415

                auth.MANAGER.sign_out(remote=False)
                raise errors.AuthExpired()
            refreshed = True
            from ..auth import manager as auth  # noqa: PLC0415

            auth.MANAGER.invalidate()
            try:
                auth.MANAGER.access_token()
            except errors.FabError:
                raise errors.AuthExpired()
            continue

        # --- rate limited -------------------------------------------------
        if resp.status_code == 429:
            wait = _retry_after(resp) or _jitter(attempt, base=30.0)
            resp.close()
            if attempt + 1 < MAX_ATTEMPTS:
                log.info("rate limited, waiting %.0fs", wait)
                time.sleep(min(wait, 600))
                continue
            raise errors.RateLimited(retry_after=wait)

        # --- server errors: retry ----------------------------------------
        if 500 <= resp.status_code < 600 and attempt + 1 < MAX_ATTEMPTS:
            resp.close()
            time.sleep(_jitter(attempt))
            continue

        return resp

    raise errors.NetworkError(str(last_exc or "request failed"))


def _jitter(attempt: int, base: float | None = None) -> float:
    delay = BACKOFF[min(attempt, len(BACKOFF) - 1)] if base is None else base * (2 ** attempt)
    return delay + random.uniform(0, 0.3)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def get_json(url: str, *, params: Optional[dict] = None, require_auth: bool = False,
             bucket: Optional[str] = None) -> dict:
    resp = request("GET", url, params=params, require_auth=require_auth, bucket=bucket)
    return _parse(resp)


def post_form(url: str, *, form: dict, require_auth: bool = True,
              bucket: Optional[str] = None) -> dict:
    resp = request("POST", url, data=form, require_auth=require_auth, bucket=bucket)
    return _parse(resp)


def _parse(resp) -> dict:
    try:
        if resp.status_code >= 400:
            body = resp.text[:400]
            raise errors.from_status(resp.status_code, body=body,
                                     retry_after=_retry_after(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise errors.ApiChanged(f"non-JSON response from {resp.url.split('?')[0]}") from exc
    finally:
        resp.close()


# ---------------------------------------------------------------------------
# binary streaming (media previews, M3 will reuse this for ZIPs)
# ---------------------------------------------------------------------------
def download_to_file(
    url: str,
    dest: str,
    *,
    read_timeout: int = 60,
    chunk: int = 64 * 1024,
    headers: Optional[dict] = None,
) -> str:
    """Stream `url` to `dest` via a `.part` file. No bearer token.

    Resume/CRC logic lives in the download layer (§8); this is the minimal
    primitive shared by thumbnail prefetch and asset fetches.
    """
    requests = _requests()
    bucket_name = bucket_for(url)
    if bucket_name in BUCKETS:
        BUCKETS[bucket_name].take()

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_ATTEMPTS):
        resp = None
        try:
            resp = session().get(
                url,
                headers=headers or {},
                timeout=(CONNECT_TIMEOUT, read_timeout),
                stream=True,
            )
            if resp.status_code == 429:
                wait = _retry_after(resp) or _jitter(attempt, base=30.0)
                if attempt + 1 < MAX_ATTEMPTS:
                    log.info("media rate limited, waiting %.0fs", wait)
                    time.sleep(min(wait, 600))
                    continue
                raise errors.RateLimited(retry_after=wait)
            if resp.status_code >= 400:
                raise errors.from_status(resp.status_code, body=resp.text[:200])

            with open(tmp, "wb") as fh:
                for block in resp.iter_content(chunk):
                    if block:
                        fh.write(block)
            os.replace(tmp, dest)
            return dest
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            log.debug("media fetch failed (%s): %s", type(exc).__name__, exc)
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(_jitter(attempt))
                continue
            break
        finally:
            if resp is not None:
                resp.close()

    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    if isinstance(last_exc, OSError):
        raise errors.ZipCorrupt(str(last_exc)) from last_exc
    raise errors.NetworkError(str(last_exc or "download failed"))

