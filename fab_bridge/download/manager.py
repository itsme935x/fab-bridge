"""The download queue (SPEC.md §8). No bpy at import time.

One worker thread per job. A job walks its stages in plain Python, streams the
signed CDN URL to a `.part` file, extracts, records the index row, and finally
hands the folder to `IMPORT_HOOK` **on the main thread** — the only place bpy is
allowed.

Failure policy: every exception becomes a typed message on the job. Nothing
escapes the worker, because there is no UI thread to catch it.
"""

from __future__ import annotations

import itertools
import os
import shutil
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..api import fab, transport
from ..core import errors, log, platform, tasks
from . import archive, resolver, store

log = log.get("download")

CHUNK = 1024 * 1024
HEADROOM = 512 * 1024 * 1024
PROGRESS_HZ = 4.0
ATTEMPTS = 3

#: Stages (SPEC.md §8.1). The UI only prints these, so they are plain strings.
QUEUED = "queued"
RESOLVING = "resolving"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
EXTRACTING = "extracting"
IMPORTING = "importing"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
FINISHED = (DONE, FAILED, CANCELLED)

#: Hosts a download may come from. Fab has used several CDNs; every one is a
#: subdomain of these. Anything else means the API changed and we must not hand
#: the URL to `requests`.
ALLOWED_HOSTS = ("epicgames.com", "epicgamescdn.com", "fab.com", "amazonaws.com")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: Set by `fab_bridge.register()`. Signature: `hook(job) -> summary string`.
IMPORT_HOOK: Optional[Callable] = None

DEFAULT_QUALITY = {"surface": "mid", "prop": "high", "plant": "mid"}


class _Cancelled(Exception):
    pass


@dataclass
class Job:
    uid: str
    title: str = ""
    quality: str = ""
    format_code: str = ""
    format_name: str = ""
    file_uid: str = ""
    filename: str = ""
    size: int = 0
    done_bytes: int = 0
    stage: str = QUEUED
    error: str = ""
    note: str = ""
    summary: str = ""
    directory: str = ""
    archive_path: str = ""
    metadata: dict = field(default_factory=dict)
    listing: object = None
    speed: float = 0.0
    started: float = 0.0
    finished_at: float = 0.0
    id: str = ""
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def active(self) -> bool:
        return self.stage not in FINISHED

    @property
    def fraction(self) -> float:
        if not self.size:
            return 0.0
        return min(1.0, self.done_bytes / self.size)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.uid, self.format_code, self.quality)

    def progress_label(self) -> str:
        parts = [self.format_name or self.format_code or "File"]
        if self.quality:
            parts.append(self.quality.title())
        if self.size:
            parts.append(f"{self.size / 1_000_000:.0f} MB")
        return " · ".join(parts)


class Manager:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._index: Optional[store.Index] = None

    # -- index ------------------------------------------------------------
    @property
    def has_index(self) -> bool:
        return self._index is not None

    def index(self, root: str | None = None) -> store.Index:
        root = root or library_root()
        if self._index is None or self._index.root != root:
            index = store.Index(root)
            index.ensure()
            self._index = index
        return self._index

    # -- queue ------------------------------------------------------------
    def enqueue(
        self,
        *,
        listing=None,
        uid: str = "",
        title: str = "",
        quality: str = "",
        format_code: str = "",
    ) -> Job:
        if listing is not None:
            uid = listing.uid
            title = listing.title
        if not uid:
            raise errors.ApiChanged("download without an asset id")
        job = Job(
            uid=uid,
            title=title or uid,
            quality=quality or "",
            format_code=format_code or "",
            listing=listing,
        )
        job.id = f"job{next(self._ids)}"
        with self._lock:
            self.jobs.append(job)
        job.started = time.monotonic()
        self._spawn(job)
        platform.mark_dirty()
        return job

    def _spawn(self, job: Job) -> None:
        thread = threading.Thread(target=self.run, args=(job,), name="fab-download", daemon=True)
        thread.start()

    def cancel(self, job_id: str) -> bool:
        for job in self.jobs:
            if job.id == job_id and job.active:
                job.cancel.set()
                job.note = "Cancelling…"
                log.info("cancel requested for %s", job.title)
                platform.mark_dirty()
                return True
        return False

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self.jobs)
            self.jobs = [job for job in self.jobs if job.active]
            removed = before - len(self.jobs)
        platform.mark_dirty()
        return removed

    def active_count(self) -> int:
        return sum(1 for job in self.jobs if job.active)

    def find(self, job_id: str) -> Optional[Job]:
        return next((job for job in self.jobs if job.id == job_id), None)

    # -- worker -----------------------------------------------------------
    def run(self, job: Job) -> None:
        """Worker entry point. Never raises."""
        try:
            self._run(job)
        except _Cancelled:
            job.stage = CANCELLED
            job.note = "Cancelled; the partial file is kept for resume"
            log.info("cancelled: %s", job.title)
        except errors.FabError as exc:
            job.stage = FAILED
            job.error = exc.user_message
            if exc.detail:
                job.note = exc.detail[:200]
            log.warning("download failed (%s): %s", type(exc).__name__, exc.detail[:200])
        except Exception as exc:  # noqa: BLE001 - a worker must never wedge
            job.stage = FAILED
            job.error = "Download failed. See the log for details."
            job.note = type(exc).__name__
            log.exception("unexpected download failure for %s", job.uid)
        finally:
            if job.stage in FINISHED:
                job.finished_at = time.time()
            job.speed = 0.0
            platform.mark_dirty()

    def _run(self, job: Job) -> None:
        listing = job.listing
        if listing is None:
            job.stage = RESOLVING
            listing = fab.get_listing(job.uid)
            job.listing = listing
        job.title = job.title or listing.title

        if job.cancel.is_set():
            raise _Cancelled()

        code = job.format_code or resolver.pick_format(listing, wanted_format())
        files = fab.get_format_files(job.uid, code)
        plan = resolver.resolve(
            listing,
            files,
            wanted_quality=job.quality or default_quality(listing),
        )

        job.quality = plan.quality or "file"
        job.format_code = plan.format_code
        job.format_name = plan.format_name
        job.file_uid = plan.file_uid
        job.filename = plan.filename
        job.size = plan.size
        job.note = plan.note
        platform.mark_dirty()

        root = library_root()
        index = self.index(root)
        cached = index.lookup(job.uid, job.format_code, job.quality)
        if cached:
            job.directory = cached
            job.metadata = archive.read_metadata(cached)
            job.stage = IMPORTING
            job.note = "Using the copy already in your library"
            tasks.post(self._import, job)
            return

        if job.cancel.is_set():
            raise _Cancelled()

        asset_dir = self._asset_dir(root, listing)
        dest = os.path.join(asset_dir, job.quality, archive.safe_name(job.format_code, "file"))
        zip_path = os.path.join(asset_dir, "_archives", self._zip_name(job))
        part = zip_path + ".part"
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)

        free = shutil.disk_usage(root).free
        if job.size and free < job.size + HEADROOM:
            raise errors.DiskFull(
                f"need {job.size / 1e9:.1f} GB, {free / 1e9:.1f} GB free in {root}"
            )

        job.stage = RESOLVING
        platform.mark_dirty()
        if job.size and os.path.exists(part) and os.path.getsize(part) > job.size:
            # A stale .part from a different tier: never resume onto it.
            os.remove(part)

        for attempt in (1, 2):
            url = self._fresh_url(job)
            job.stage = DOWNLOADING
            job.done_bytes = os.path.getsize(part) if os.path.exists(part) else 0
            platform.mark_dirty()
            self._stream(job, url, part)
            job.stage = VERIFYING
            platform.mark_dirty()
            try:
                archive.verify_zip(part)
                break
            except errors.ZipCorrupt:
                try:
                    os.remove(part)
                except OSError:
                    pass
                job.done_bytes = 0
                if attempt == 2:
                    raise
                job.note = "The archive was corrupted; downloading it again"
                log.warning("corrupt zip for %s, retrying", job.uid)

        if job.cancel.is_set():
            raise _Cancelled()

        job.stage = EXTRACTING
        platform.mark_dirty()
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        written = archive.safe_extract(part, dest)
        if not written:
            raise errors.ZipCorrupt("the archive held no files")

        job.directory = dest
        job.metadata = archive.read_metadata(dest)
        job.archive_path = zip_path if keep_archives() else ""
        index.record(
            job.uid,
            job.title,
            seller=getattr(listing, "seller", ""),
            listing_type=getattr(listing, "listing_type", ""),
            category=getattr(listing, "category_name", ""),
            format_code=job.format_code,
            quality=job.quality,
            path=dest,
            size=job.size,
            filename=plan.filename,
        )

        if not keep_archives():
            for path in (part, zip_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(os.path.dirname(zip_path))
            except OSError:
                pass

        job.stage = IMPORTING
        job.summary = f"Extracted {len(written)} files"
        platform.mark_dirty()
        tasks.post(self._import, job)

    def _fresh_url(self, job: Job) -> str:
        url = fab.download_url(job.uid, job.format_code, job.file_uid, require_auth=True)
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in LOOPBACK_HOSTS:
            # Only the unit tests point the stream at a local server. Fab never
            # returns a loopback URL, so this cannot weaken a real download.
            return url
        if parts.scheme != "https":
            raise errors.ApiChanged(f"download URL is not https ({parts.scheme!r})")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
            raise errors.ApiChanged(f"unexpected download host {host!r}")
        return url

    def _asset_dir(self, root: str, listing) -> str:
        short = listing.uid.split("-")[0][:8]
        folder = f"{archive.safe_name(listing.title)} [{short}]"
        return os.path.join(root, folder)

    def _zip_name(self, job: Job) -> str:
        stem = archive.safe_name(job.filename or job.title, "asset.zip")
        if not stem.lower().endswith(".zip"):
            stem += ".zip"
        return f"{job.quality or 'file'}-{stem}"

    def _stream(self, job: Job, url: str, part: str) -> None:
        """Resume-aware stream into `part`. Keeps the file on cancel."""
        requests = transport.requests_module()
        sess = transport.session()
        transport.BUCKETS["download"].take()

        existing = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}

        resp = None
        last_exc: Optional[Exception] = None
        for attempt in range(ATTEMPTS):
            try:
                resp = sess.get(
                    url,
                    headers=headers,
                    timeout=(transport.CONNECT_TIMEOUT, 60),
                    stream=True,
                )
                if resp.status_code == 429 and attempt + 1 < ATTEMPTS:
                    resp.close()
                    time.sleep(transport._retry_after(resp) or transport._jitter(attempt, base=15.0))
                    continue
                if resp.status_code == 416:
                    resp.close()
                    if job.size and existing >= job.size:
                        return
                    existing = 0
                    headers = {}
                    if os.path.exists(part):
                        os.remove(part)
                    continue
                if resp.status_code >= 400:
                    raise errors.from_status(resp.status_code, body=resp.text[:200])
                break
            except requests.RequestException as exc:
                last_exc = exc
                resp = None
                if attempt + 1 < ATTEMPTS:
                    time.sleep(transport._jitter(attempt))
                    continue
                raise errors.NetworkError(str(exc)) from exc

        if resp is None:
            raise errors.NetworkError(str(last_exc or "download failed"))

        try:
            resumed = resp.status_code == 206
            if not resumed:
                existing = 0
                headers = {}
            length = int(resp.headers.get("Content-Length") or 0)
            total = existing + length if length else job.size
            if total:
                job.size = max(total, existing)
            job.done_bytes = existing

            mode = "ab" if resumed else "wb"
            written = existing
            window_start = time.monotonic()
            window_bytes = 0
            last_emit = 0.0
            with open(part, mode) as handle:
                for block in resp.iter_content(CHUNK):
                    if job.cancel.is_set():
                        raise _Cancelled()
                    if not block:
                        continue
                    handle.write(block)
                    written += len(block)
                    window_bytes += len(block)
                    job.done_bytes = written
                    now = time.monotonic()
                    if now - window_start >= 0.5:
                        job.speed = window_bytes / (now - window_start)
                        window_start = now
                        window_bytes = 0
                    if now - last_emit >= 1.0 / PROGRESS_HZ:
                        last_emit = now
                        platform.mark_dirty()
            job.done_bytes = written
            job.speed = 0.0
        finally:
            resp.close()

    # -- main thread ------------------------------------------------------
    def _import(self, job: Job) -> None:
        if IMPORT_HOOK is None:
            job.stage = DONE
            job.summary = "Downloaded; import the folder from your library"
            platform.mark_dirty()
            return
        try:
            job.summary = IMPORT_HOOK(job) or "Imported"
            job.stage = DONE
            log.info("imported %s (%s)", job.title, job.summary)
        except Exception as exc:  # noqa: BLE001 - shown on the job
            job.stage = FAILED
            job.error = getattr(exc, "user_message", "") or "Import failed."
            job.note = type(exc).__name__
            log.exception("import failed for %s", job.uid)
            if isinstance(exc, errors.FabError) and exc.detail:
                job.note = exc.detail[:200]
        finally:
            job.finished_at = time.time()
            platform.mark_dirty()


MANAGER = Manager()


# ---------------------------------------------------------------------------
# policy helpers
# ---------------------------------------------------------------------------
def wanted_format() -> Optional[str]:
    """`prefs.prefer_format` as a format code, or None for the §7.1 chain."""
    try:
        from ..ui import prefs

        settings = prefs.get()
    except Exception:  # noqa: BLE001 - headless or unregistered
        settings = None
    choice = getattr(settings, "prefer_format", "auto") if settings else "auto"
    return {"converted": "converted-files", "fbx": "fbx", "gltf": "gltf"}.get(choice)


def default_quality(listing) -> str:
    kinds = set(getattr(listing, "format_codes", ()))
    if "texture-set" in kinds:
        kind = "surface"
    elif resolver.is_plant(listing):
        kind = "plant"
    else:
        kind = "prop"
    try:
        from ..ui import prefs

        settings = prefs.get()
    except Exception:  # noqa: BLE001
        settings = None
    attribute = {
        "surface": "default_quality_surface",
        "prop": "default_quality_prop",
        "plant": "default_quality_plant",
    }[kind]
    configured = getattr(settings, attribute, "") if settings else ""
    return configured or DEFAULT_QUALITY[kind]


def keep_archives() -> bool:
    try:
        from ..ui import prefs

        settings = prefs.get()
    except Exception:  # noqa: BLE001
        settings = None
    return bool(getattr(settings, "keep_archives", False)) if settings else False


def library_root() -> str:
    """The library folder, created on first use."""
    try:
        from ..ui import prefs

        settings = prefs.get()
        configured = getattr(settings, "library_root", "") if settings else ""
    except Exception:  # noqa: BLE001
        configured = ""
    root = configured or os.path.join("~", "Documents", "FabAssets")
    root = os.path.abspath(os.path.expandvars(os.path.expanduser(root)))
    os.makedirs(root, exist_ok=True)
    return root


def shutdown() -> None:
    """unregister(): cancel workers and drop the index handle."""
    for job in MANAGER.jobs:
        if job.active:
            job.cancel.set()
    MANAGER._index = None
