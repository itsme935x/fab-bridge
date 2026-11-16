"""Token persistence (SPEC.md §6.4).

Windows  -> DPAPI (CryptProtectData), tied to the user account
macOS    -> Keychain via `security`
Linux    -> `secret-tool` if present, else a 0600 file in the config dir

Nothing here imports bpy at module scope so the store can be tested headless.
The payload is the whole TokenBundle as JSON; if the stored `client_id` no
longer matches the running build, the session is discarded (§6.4).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from ..core import log
from .models import TokenBundle

log = log.get("store")

SERVICE = "fab_bridge"
FILENAME = "session.bin"
PLAINTEXT_HEADER = b"FBPLAIN1"  # marks an unencrypted payload when no keystore exists


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
def config_dir() -> str:
    """Resolved once, on the main thread.

    `bpy.utils.user_resource` must not be called from a download worker, so the
    first caller (register/load) pins the path for the rest of the session.
    """
    global _CONFIG_DIR
    if _CONFIG_DIR is not None:
        return _CONFIG_DIR

    try:
        import bpy  # noqa: PLC0415

        base = bpy.utils.user_resource("CONFIG")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "fab_bridge")
    os.makedirs(path, exist_ok=True)
    _CONFIG_DIR = path
    return path


_CONFIG_DIR: Optional[str] = None


def _session_path() -> str:
    return os.path.join(config_dir(), FILENAME)


# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------
def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def make_blob(raw: bytes):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    blob_in, keepalive = make_blob(data)
    blob_out = DATA_BLOB()

    # CryptProtectData and CryptUnprotectData share a signature: the second
    # argument is an out-param (description) and may be NULL.
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
        del keepalive


# ---------------------------------------------------------------------------
# macOS keychain
# ---------------------------------------------------------------------------
def _macos_security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["security", *args], input=stdin, capture_output=True, text=True, timeout=20
    )


def _macos_write(payload: str) -> bool:
    if not shutil.which("security"):
        return False
    # -U updates an existing item instead of failing.
    proc = _macos_security(
        "add-generic-password", "-U", "-s", SERVICE, "-a", SERVICE, "-w", payload
    )
    return proc.returncode == 0


def _macos_read() -> Optional[str]:
    if not shutil.which("security"):
        return None
    proc = _macos_security("find-generic-password", "-s", SERVICE, "-a", SERVICE, "-w")
    return proc.stdout.strip() if proc.returncode == 0 else None


def _macos_delete() -> None:
    if shutil.which("security"):
        _macos_security("delete-generic-password", "-s", SERVICE, "-a", SERVICE)


# ---------------------------------------------------------------------------
# Linux secret-tool
# ---------------------------------------------------------------------------
def _lin_write(payload: str) -> bool:
    if not shutil.which("secret-tool"):
        return False
    proc = subprocess.run(
        ["secret-tool", "store", "--label=Fab Bridge", "service", SERVICE, "account", SERVICE],
        input=payload,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return proc.returncode == 0


def _lin_read() -> Optional[str]:
    if not shutil.which("secret-tool"):
        return None
    proc = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "account", SERVICE],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _lin_delete() -> None:
    if shutil.which("secret-tool"):
        subprocess.run(
            ["secret-tool", "clear", "service", SERVICE, "account", SERVICE],
            capture_output=True,
            timeout=20,
        )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def save(bundle: TokenBundle) -> None:
    payload = json.dumps(bundle.to_dict(), separators=(",", ":")).encode("utf-8")
    path = _session_path()
    tmp = path + ".tmp"

    if sys.platform == "win32":
        blob = _dpapi(payload, encrypt=True)
        _write_file(tmp, blob)
    elif sys.platform == "darwin" and _macos_write(payload.decode()):
        return
    elif sys.platform.startswith("linux") and _lin_write(payload.decode()):
        return
    else:
        _write_file(tmp, PLAINTEXT_HEADER + payload)

    os.replace(tmp, path)
    log.debug("session saved (%d bytes)", len(payload))


def load(expected_client_id: str | None = None) -> Optional[TokenBundle]:
    path = _session_path()
    raw: Optional[bytes] = None

    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            log.warning("could not read session file")
            return None
    elif sys.platform == "darwin":
        text = _macos_read()
        raw = text.encode() if text else None
    elif sys.platform.startswith("linux"):
        text = _lin_read()
        raw = text.encode() if text else None

    if not raw:
        return None

    try:
        if sys.platform == "win32" and not raw.startswith(PLAINTEXT_HEADER):
            raw = _dpapi(raw, encrypt=False)
        elif raw.startswith(PLAINTEXT_HEADER):
            raw = raw[len(PLAINTEXT_HEADER):]
        bundle = TokenBundle.from_dict(json.loads(raw.decode("utf-8")))
    except Exception as exc:  # corrupted, wrong user, format change
        log.warning("discarding unreadable session: %s", type(exc).__name__)
        clear()
        return None

    if expected_client_id and bundle.client_id != expected_client_id:
        log.info("stored session belongs to a different client; signing out")
        clear()
        return None

    return bundle


def clear() -> None:
    path = _session_path()
    for candidate in (path, path + ".tmp"):
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError:
            log.warning("could not remove %s", candidate)
    if sys.platform == "darwin":
        _macos_delete()
    elif sys.platform.startswith("linux"):
        _lin_delete()


def _write_file(path: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
