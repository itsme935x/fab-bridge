"""Typed error taxonomy (SPEC.md §11).

Every failure that reaches the UI is one of these. Nothing else is allowed to
escape the api/auth/download layers, because the panel renders a fixed message
per class and a raw traceback is never useful to a user.
"""

from __future__ import annotations


class FabError(Exception):
    """Base class. `message` is user-facing; `detail` is for the log."""

    user_message = "Something went wrong talking to Fab."

    def __init__(self, detail: str = "", *, retry_after: float | None = None):
        super().__init__(detail or self.user_message)
        self.detail = detail
        self.retry_after = retry_after

    def __str__(self) -> str:
        return self.user_message


class AuthRequired(FabError):
    user_message = "Sign in to Fab to download assets."


class AuthExpired(FabError):
    user_message = "Your Epic session expired. Sign in again."


class OnlineAccessDisabled(FabError):
    user_message = "Online access is off. Enable Preferences > System > Network."


class RateLimited(FabError):
    user_message = "Fab is rate-limiting requests. Try again shortly."

    def __init__(self, detail: str = "", *, retry_after: float | None = None):
        super().__init__(detail, retry_after=retry_after)
        if retry_after:
            self.user_message = f"Fab is rate-limiting requests. Try again in {int(retry_after)} s."


class NotEntitled(FabError):
    user_message = "This asset isn't in your library. Claim it on Fab first."


class UnsupportedFormat(FabError):
    user_message = "This listing has no Blender-importable files (UE-only)."


class ApiChanged(FabError):
    user_message = "Fab's API changed. Check for an addon update."


class DiskFull(FabError):
    user_message = "Not enough free space."


class ZipCorrupt(FabError):
    user_message = "Download was corrupted. Delete and retry."


class ImportFailed(FabError):
    user_message = "Import failed."


class NetworkError(FabError):
    user_message = "Network error. Check your connection."


class NotFound(FabError):
    user_message = "That asset is no longer on Fab."


class Forbidden(FabError):
    user_message = "Fab refused that request."


STATUS_MAP = {
    401: AuthExpired,
    403: Forbidden,
    404: NotFound,
    429: RateLimited,
}


def from_status(status: int, *, body: str = "", retry_after: float | None = None) -> FabError:
    """Map an HTTP status to the closest taxonomy entry."""
    cls = STATUS_MAP.get(status)
    if cls is RateLimited:
        return RateLimited(body, retry_after=retry_after)
    if cls is not None:
        return cls(body)
    if 500 <= status < 600:
        return NetworkError(f"HTTP {status}: {body[:200]}")
    return ApiChanged(f"HTTP {status}: {body[:200]}")
