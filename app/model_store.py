"""Transcription model downloads.

Models are only ever downloaded when a user clicks "Download" in the UI. Once
on disk, the worker loads them with ``local_files_only=True``, so transcription
never contacts the internet.

The web container reads/writes download *requests* in the ``whisper_models``
table. The worker (the only container with the models volume) scans the disk,
performs downloads in a background thread and reports progress.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import os
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import session_scope
from .models import (
    MODEL_DOWNLOADING,
    MODEL_ERROR,
    MODEL_MISSING,
    MODEL_QUEUED,
    MODEL_READY,
    WhisperModelState,
)

log = logging.getLogger("stt.models")

# Same file set faster_whisper.utils.download_model fetches.
ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
STALE_SECONDS = 120  # a "downloading" row with no heartbeat for this long is reclaimed
DISK_HEADROOM = 1.05


class ModelNotDownloaded(RuntimeError):
    pass


# --------------------------------------------------------------------------- shared (web + worker)


def states(db: Session) -> dict[str, WhisperModelState]:
    rows = db.scalars(select(WhisperModelState).where(WhisperModelState.name.in_(settings.whisper_models))).all()
    return {r.name: r for r in rows}


def state_out(name: str, row: WhisperModelState | None) -> dict:
    if row is None:
        return {"status": MODEL_MISSING, "bytes_done": 0, "bytes_total": None, "error": None, "ready": False}
    return {
        "status": row.status,
        "bytes_done": row.bytes_done,
        "bytes_total": row.bytes_total,
        "error": row.error,
        "ready": row.status == MODEL_READY,
    }


def is_ready(db: Session, name: str) -> bool:
    row = db.get(WhisperModelState, name)
    return row is not None and row.status == MODEL_READY


def request_download(db: Session, name: str, user_id: int | None) -> WhisperModelState:
    """Queue a download (idempotent: ready/queued/downloading models are left alone)."""
    row = db.get(WhisperModelState, name, with_for_update=True)
    if row is None:
        row = WhisperModelState(name=name, status=MODEL_MISSING, bytes_done=0)
        db.add(row)
    if row.status in (MODEL_MISSING, MODEL_ERROR):
        row.status = MODEL_QUEUED
        row.error = None
        row.bytes_done = 0
        row.requested_by = user_id
    db.commit()
    return row


# --------------------------------------------------------------------------- worker: disk helpers
# These three hooks are the only places that touch faster-whisper / Hugging Face,
# so tests can replace them.


def _local_path(name: str) -> str | None:
    """Path of a complete local copy, or None. Never touches the network."""
    from faster_whisper.utils import download_model

    try:
        return download_model(name, local_files_only=True, cache_dir=str(settings.models_dir))
    except Exception:
        return None


def _expected_size(name: str) -> int | None:
    """Total download size in bytes (asked from Hugging Face), or None if unknown."""
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub import HfApi

        repo = _MODELS.get(name, name)
        info = HfApi().model_info(repo, files_metadata=True)
        total = sum(
            (f.size or 0)
            for f in info.siblings or []
            if any(fnmatch.fnmatch(f.rfilename, pat) for pat in ALLOW_PATTERNS)
        )
        return total or None
    except Exception as exc:  # unknown size just means an indeterminate progress bar
        log.info("Couldn't get size for %s: %s", name, exc)
        return None


def _download(name: str) -> str:
    from faster_whisper.utils import download_model

    return download_model(name, local_files_only=False, cache_dir=str(settings.models_dir))


def dir_size(path: Path) -> int:
    """Bytes used under ``path``, not following symlinks (the HF cache symlinks
    snapshots to blobs; following them would double-count)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                st = os.lstat(os.path.join(root, f))
            except OSError:
                continue
            if not os.path.islink(os.path.join(root, f)):
                total += st.st_size
    return total


def model_size(path: str | Path) -> int:
    """Size of a downloaded model snapshot (follows the cache's symlinks to blobs)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, f))
    return total


# --------------------------------------------------------------------------- worker: scan


def scan_models() -> dict[str, str]:
    """Record which configured models are on disk. Called on worker start.

    Interrupted downloads are re-queued so they resume.
    """
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for name in settings.whisper_models:
        path = _local_path(name)
        present = path is not None
        with session_scope() as db:
            row = db.get(WhisperModelState, name, with_for_update=True)
            if row is None:
                row = WhisperModelState(name=name, status=MODEL_MISSING, bytes_done=0)
                db.add(row)
            if present:
                size = model_size(path)
                row.status = MODEL_READY
                row.error = None
                row.bytes_total = row.bytes_done = size
            elif row.status == MODEL_DOWNLOADING:
                row.status = MODEL_QUEUED  # interrupted by a restart: resume
            elif row.status == MODEL_READY:
                row.status = MODEL_MISSING  # files were removed from the volume
                row.bytes_done = 0
            result[name] = row.status
    log.info("Models on disk: %s", ", ".join(f"{n}={s}" for n, s in result.items()))
    return result


def mark_missing(name: str, reason: str) -> None:
    with session_scope() as db:
        row = db.get(WhisperModelState, name)
        if row is not None:
            row.status = MODEL_MISSING
            row.bytes_done = 0
            row.error = reason[:2000]


# --------------------------------------------------------------------------- worker: downloads


def claim_download() -> str | None:
    stale = datetime.now(UTC) - timedelta(seconds=STALE_SECONDS)
    with session_scope() as db:
        row = db.scalar(
            select(WhisperModelState)
            .where(
                WhisperModelState.name.in_(settings.whisper_models),
                or_(
                    WhisperModelState.status == MODEL_QUEUED,
                    (WhisperModelState.status == MODEL_DOWNLOADING) & (WhisperModelState.heartbeat_at < stale),
                ),
            )
            .order_by(WhisperModelState.updated_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.status = MODEL_DOWNLOADING
        row.bytes_done = 0
        row.error = None
        row.heartbeat_at = datetime.now(UTC)
        return row.name


def _update(name: str, **values) -> None:
    with session_scope() as db:
        row = db.get(WhisperModelState, name)
        if row is not None:
            for k, v in values.items():
                setattr(row, k, v)
            row.heartbeat_at = datetime.now(UTC)


def _human(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def run_download(name: str, poll_seconds: float = 1.0) -> bool:
    """Download one model, reporting progress. Returns True on success."""
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    if _local_path(name):
        _update(name, status=MODEL_READY, error=None)
        return True

    total = _expected_size(name)
    _update(name, bytes_total=total)
    if total:
        free = shutil.disk_usage(settings.models_dir).free
        if free < total * DISK_HEADROOM:
            _update(
                name, status=MODEL_ERROR, error=f"Not enough disk space: needs {_human(total)}, {_human(free)} free."
            )
            return False

    log.info("Downloading model %s (%s)", name, _human(total) if total else "size unknown")
    baseline = dir_size(settings.models_dir)
    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["path"] = _download(name)
        except Exception as exc:  # reported below
            outcome["error"] = exc

    t = threading.Thread(target=worker, name=f"download-{name}", daemon=True)
    started = time.monotonic()
    t.start()
    while t.is_alive():
        t.join(poll_seconds)
        done = max(0, dir_size(settings.models_dir) - baseline)
        if total:
            done = min(done, total)
        _update(name, bytes_done=done)

    if "error" in outcome or not _local_path(name):
        exc = outcome.get("error")
        msg = f"Download failed: {exc}" if exc else "Download finished but the model files are incomplete."
        log.warning("Model %s: %s", name, msg)
        _update(name, status=MODEL_ERROR, error=msg[:2000], bytes_done=0)
        return False

    size = total or max(0, dir_size(settings.models_dir) - baseline)
    _update(name, status=MODEL_READY, error=None, bytes_done=size, bytes_total=size)
    log.info("Model %s downloaded in %.0fs", name, time.monotonic() - started)
    return True


def download_loop(stop: threading.Event, poll_seconds: float = 2.0) -> None:
    """Background thread in the worker: process download requests one at a time."""
    while not stop.is_set():
        try:
            name = claim_download()
            if name:
                run_download(name)
                continue
        except Exception:
            log.exception("Download loop error")
        stop.wait(poll_seconds)
