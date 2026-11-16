"""Token bundle model (SPEC.md §6.1). No bpy, no network — safe to unit test."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional

# Refresh a little before the token actually dies, and never more often than
# this even if a caller asks (SPEC.md §6.5).
REFRESH_MARGIN = 600
MIN_REFRESH_INTERVAL = 60


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float
    account_id: str
    display_name: str
    client_id: str

    # --- expiry -----------------------------------------------------------
    def is_access_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def needs_refresh(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at - REFRESH_MARGIN

    def can_refresh(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.refresh_expires_at

    # --- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenBundle":
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=float(data.get("expires_at", 0)),
            refresh_expires_at=float(data.get("refresh_expires_at", 0)),
            account_id=str(data.get("account_id", "")),
            display_name=str(data.get("display_name", "")),
            client_id=str(data.get("client_id", "")),
        )

    def with_tokens(self, other: "TokenBundle") -> "TokenBundle":
        return replace(self, **other.to_dict())


def _seconds(payload: dict, *keys: str) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _epoch(payload: dict, *keys: str) -> Optional[float]:
    """Absolute expiry: epoch seconds, or Epic's `2026-11-16T17:14:05.891Z`."""
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            continue
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def parse_oauth_response(payload: dict, client_id: str, *, now: float | None = None) -> TokenBundle:
    """Turn an Epic OAuth response into a bundle.

    Epic mixes the two conventions (measured 2026-11-16 on a real
    `authorization_code` grant):
      access  -> `expires_in` (seconds) + `expires_at` (ISO string)
      refresh -> `refresh_expires` (seconds) + `refresh_expires_at` (ISO string)
    Note `refresh_expires`, not `refresh_expires_in`. Both conventions are
    accepted, and an ISO timestamp is never fed to `float()`.
    """
    now = now or time.time()

    expires_at = _seconds(payload, "expires_in")
    if expires_at is not None:
        expires_at += now
    else:
        expires_at = _epoch(payload, "expires_at") or 0.0

    refresh_expires_at = _seconds(payload, "refresh_expires_in", "refresh_expires")
    if refresh_expires_at is not None:
        refresh_expires_at += now
    else:
        refresh_expires_at = _epoch(payload, "refresh_expires_at") or 0.0

    if not payload.get("access_token"):
        raise ValueError("token response has no access_token")

    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_at=expires_at,
        refresh_expires_at=refresh_expires_at,
        account_id=payload.get("account_id", ""),
        display_name=payload.get("displayName", ""),
        client_id=client_id,
    )
