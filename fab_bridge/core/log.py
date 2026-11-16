"""Logging with a redaction filter.

Security invariant (§Appendix C): never log tokens, auth codes, or signed URLs.
The filter is applied to every record, so a careless `log.info(url)` cannot leak
an `f_token`.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re

LOG = "fab_bridge"

# Anything that looks like a credential in a URL or a form body.
_SECRETS = re.compile(
    r"(?i)\b("
    r"access_token|refresh_token|authorization_code|code|f_token|cf_token|"
    r"authorization|token|password|client_secret"
    r")=([^&\s\"']+)"
)

_QUERY = re.compile(r"\?[^\s\"']+")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        msg = _SECRETS.sub(r"\1=<redacted>", msg)
        msg = _QUERY.sub("?<redacted>", msg)
        record.msg = msg
        record.args = ()
        return True


def log_dir() -> str:
    """Per-user config dir, without importing bpy at module scope."""
    try:
        import bpy  # noqa: PLC0415 - optional, absent in plain-python tests

        base = bpy.utils.user_resource("CONFIG")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "fab_bridge")
    os.makedirs(path, exist_ok=True)
    return path


def setup(level: str = "info") -> logging.Logger:
    """Idempotent: calling this twice (i.e. on every enable / script reload)
    must not stack handlers on the shared logger object."""
    logger = logging.getLogger(LOG)
    logger.setLevel(_level(level))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    stream.addFilter(RedactFilter())
    logger.addHandler(stream)

    try:
        rotating = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir(), "fab_bridge.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        rotating.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(message)s")
        )
        rotating.addFilter(RedactFilter())
        logger.addHandler(rotating)
    except OSError:
        # A read-only config dir must not stop the addon from working.
        pass

    return logger


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOG}.{name}" if name else LOG)


def _level(name: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }.get(str(name).lower(), logging.INFO)
