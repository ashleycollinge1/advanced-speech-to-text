"""Media file layout, ffmpeg helpers and HTTP Range streaming."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .config import settings

PLAYBACK_NAME = "playback.m4a"
PLAYBACK_MIME = "audio/mp4"
CHUNK = 256 * 1024

ALLOWED_EXTS = {
    "mp3", "m4a", "mp4", "aac", "wav", "flac", "ogg", "oga", "opus", "webm",
    "wma", "amr", "aiff", "aif", "caf", "mkv", "mov", "3gp",
}  # fmt: skip


def recording_dir(recording_id: uuid.UUID) -> Path:
    return settings.media_dir / str(recording_id)


def original_path(recording_id: uuid.UUID, ext: str) -> Path:
    return recording_dir(recording_id) / f"original.{ext}"


def playback_path(recording_id: uuid.UUID) -> Path:
    return recording_dir(recording_id) / PLAYBACK_NAME


def remove_recording_media(recording_id: uuid.UUID) -> None:
    d = recording_dir(recording_id)
    # Guard against anything odd escaping the media root.
    if d.resolve().parent == settings.media_dir.resolve() and d.exists():
        shutil.rmtree(d, ignore_errors=True)


def safe_ext(filename: str) -> str | None:
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", filename or "")
    ext = m.group(1).lower() if m else None
    return ext if ext in ALLOWED_EXTS else None


# --------------------------------------------------------------------------- ffmpeg


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - depends on image
        raise MediaError(f"{cmd[0]} is not installed") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise MediaError(f"{cmd[0]} failed: {' / '.join(tail) or 'unknown error'}")
    return proc


def probe_duration(path: Path) -> float | None:
    proc = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        timeout=120,
    )
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None


def make_playback(src: Path, dst: Path) -> None:
    """Transcode any input into mono AAC/M4A: plays in every browser, seeks well,
    and is the exact file we transcribe, so timestamps line up with playback."""
    tmp = dst.with_suffix(".tmp.m4a")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(tmp),
        ]  # fmt: skip
    )
    tmp.replace(dst)


def decode_pcm16k(src: Path):
    """Decode to 16 kHz mono float32 (what Whisper expects) using ffmpeg.

    Avoids faster-whisper's PyAV decoder, whose API compatibility shifts
    between releases. Memory: ~230 MB per hour of audio.
    """
    import numpy as np  # worker-only dependency

    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000"]
            + ["-f", "f32le", "-"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise MediaError("ffmpeg is not installed") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise MediaError(f"ffmpeg failed: {' / '.join(tail) or 'unknown error'}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def make_clip(src: Path, start: float, end: float, dst: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(src),
            "-t",
            f"{max(0.1, end - start):.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(dst),
        ],  # fmt: skip
        timeout=300,
    )


# --------------------------------------------------------------------------- range streaming

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def range_response(request: Request, path: Path, media_type: str, filename: str | None = None) -> Response:
    if not path.is_file():
        raise HTTPException(404, "Audio not available.")
    size = path.stat().st_size
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
    }
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    rng = request.headers.get("range")
    if rng:
        m = _RANGE_RE.match(rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
            end = size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        length = end - start + 1
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
        return StreamingResponse(
            _iter_file(path, start, length), status_code=206, media_type=media_type, headers=headers
        )

    headers["Content-Length"] = str(size)
    return StreamingResponse(_iter_file(path, 0, size), media_type=media_type, headers=headers)
