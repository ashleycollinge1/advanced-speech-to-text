"""Transcription worker.

Jobs are recordings in status 'queued'. Each one is claimed with
SELECT ... FOR UPDATE SKIP LOCKED, so several workers can run safely and no
broker is needed. A heartbeat lets crashed jobs be reclaimed.
"""

from __future__ import annotations

import gc
import logging
import signal
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update

from . import media, model_store, stt_models
from .config import settings
from .db import session_scope
from .models import STATUS_DONE, STATUS_ERROR, STATUS_PROCESSING, STATUS_QUEUED, Recording, Segment
from .vocab import fit_hotwords

log = logging.getLogger("stt.worker")

_stop = False
# One model in memory at a time: large models need several GB of RAM.
_model = None
_model_name: str | None = None


class Cancelled(Exception):
    """The recording was deleted while it was being processed."""


def _handle_signal(signum, _frame):
    global _stop
    log.info("Signal %s received; finishing current job then exiting.", signum)
    _stop = True


def get_model(name: str):
    """Return the loaded model, swapping out the previous one if a different model is needed."""
    global _model, _model_name
    if _model is not None and _model_name == name:
        return _model
    from faster_whisper import WhisperModel  # heavy import: only in the worker

    if model_store._local_path(name) is None:
        model_store.mark_missing(name, "Model files not found on the worker.")
        raise model_store.ModelNotDownloaded(name)

    if _model is not None:
        log.info("Unloading model %s", _model_name)
        _model = None
        _model_name = None
        gc.collect()
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    log.info("Loading model %s (%s) from disk", name, settings.whisper_compute_type)
    started = time.monotonic()
    _model = WhisperModel(
        name,
        device="cpu",
        compute_type=settings.whisper_compute_type,
        cpu_threads=settings.whisper_threads,
        download_root=str(settings.models_dir),
        local_files_only=True,  # never go online: downloads only happen when a user asks
    )
    _model_name = name
    log.info("Model %s ready in %.0fs.", name, time.monotonic() - started)
    return _model


def claim_job() -> uuid.UUID | None:
    stale = datetime.now(UTC) - timedelta(minutes=settings.worker_stale_minutes)
    with session_scope() as db:
        rec = db.scalar(
            select(Recording)
            .where(
                or_(
                    Recording.status == STATUS_QUEUED,
                    (Recording.status == STATUS_PROCESSING) & (Recording.heartbeat_at < stale),
                )
            )
            .order_by(Recording.created_at)
            .limit(1)
            .with_for_update(skip_locked=True, of=Recording)
        )
        if rec is None:
            return None
        if rec.attempts >= settings.max_attempts:
            rec.status = STATUS_ERROR
            rec.error = rec.error or "Gave up after repeated failures (the worker may have run out of memory)."
            return None
        rec.status = STATUS_PROCESSING
        rec.attempts += 1
        rec.progress = 0.0
        rec.error = None
        rec.heartbeat_at = datetime.now(UTC)
        return rec.id


def _touch(rec_id: uuid.UUID, **values) -> None:
    with session_scope() as db:
        res = db.execute(
            update(Recording).where(Recording.id == rec_id).values(heartbeat_at=datetime.now(UTC), **values)
        )
        if res.rowcount == 0:
            raise Cancelled()


def process(rec_id: uuid.UUID) -> None:
    with session_scope() as db:
        rec = db.get(Recording, rec_id)
        if rec is None:
            raise Cancelled()
        ext = rec.original_ext
        model_name = stt_models.resolve(rec.model)
        vocabulary = rec.vocabulary

    src = media.original_path(rec_id, ext)
    play = media.playback_path(rec_id)
    if not src.is_file():
        raise media.MediaError("The original upload is missing from the media folder.")

    if not play.is_file():
        log.info("[%s] transcoding", rec_id)
        media.make_playback(src, play)
    duration = media.probe_duration(play) or 0.0
    _touch(rec_id, duration=duration, has_playback=True, progress=0.02, model=model_name)

    model = get_model(model_name)
    log.info("[%s] decoding", rec_id)
    pcm = media.decode_pcm16k(play)
    if duration <= 0:
        duration = len(pcm) / 16000
    log.info("[%s] transcribing %.0fs of audio with %s", rec_id, duration, model_name)
    started = time.monotonic()
    hotwords = hotwords_for(model, vocabulary, rec_id)
    seg_iter, info = model.transcribe(
        pcm,
        language="en",
        word_timestamps=True,
        vad_filter=True,
        beam_size=settings.whisper_beam_size,
        condition_on_previous_text=False,  # avoids repetition loops on long audio
        # Hotwords (not initial_prompt): faster-whisper re-sends them for every 30s
        # window, whereas initial_prompt is dropped after the first window when
        # condition_on_previous_text is False.
        hotwords=hotwords,
    )
    total = duration or info.duration or 1.0

    rows: list[dict] = []
    last_report = 0.0
    for seg in seg_iter:
        words = merge_tokens((w.start, w.end, w.word) for w in (seg.words or []))
        text = " ".join(w[2] for w in words) if words else seg.text.strip()
        if not text:
            continue
        rows.append(
            {"idx": len(rows), "start": round(seg.start, 3), "end": round(seg.end, 3), "text": text, "words": words}
        )
        now = time.monotonic()
        if now - last_report > 3:
            last_report = now
            _touch(rec_id, progress=min(0.99, 0.02 + 0.97 * (seg.end / total)))
        if _stop:
            raise InterruptedError("Worker shutting down")

    save_result(rec_id, rows, model_name)
    log.info(
        "[%s] %s took %.0fs (%.2fx real time)",
        rec_id,
        model_name,
        time.monotonic() - started,
        (time.monotonic() - started) / max(duration, 0.1),
    )
    log.info("[%s] done: %d segments", rec_id, len(rows))


def hotwords_for(model, vocabulary: str | None, rec_id: uuid.UUID | None = None) -> str | None:
    """Vocabulary hints sized to the model's real token budget."""
    hf = getattr(model, "hf_tokenizer", None)
    encode = (lambda text: hf.encode(text, add_special_tokens=False).ids) if hf is not None else None
    hotwords, dropped = fit_hotwords(vocabulary, encode)
    if dropped:
        log.warning("[%s] vocabulary over token budget; dropped: %s", rec_id, ", ".join(dropped))
    if hotwords:
        log.info("[%s] using %d vocabulary hint(s)", rec_id, hotwords.count(",") + 1)
    return hotwords


def merge_tokens(tokens) -> list[list]:
    """Whisper marks word boundaries with a leading space. Tokens without one
    ("%", "'s", "-based") belong to the previous word, so join them to keep the
    displayed text faithful ("12%" not "12 %")."""
    out: list[list] = []
    for start, end, raw in tokens:
        text = raw.strip()
        if not text:
            continue
        if out and not raw[:1].isspace():
            out[-1][1] = round(end, 3)
            out[-1][2] += text
        else:
            out.append([round(start, 3), round(end, 3), text])
    return out


def save_result(rec_id: uuid.UUID, rows: list[dict], model_name: str | None = None) -> None:
    """Atomically replace the transcript and mark the recording done."""
    with session_scope() as db:
        rec = db.scalar(select(Recording).where(Recording.id == rec_id).with_for_update(of=Recording))
        if rec is None:
            raise Cancelled()
        db.execute(delete(Segment).where(Segment.recording_id == rec_id))
        db.add_all(Segment(recording_id=rec_id, **r) for r in rows)
        rec.status = STATUS_DONE
        rec.progress = 1.0
        rec.model = model_name or stt_models.resolve(rec.model)
        rec.completed_at = datetime.now(UTC)
        rec.heartbeat_at = datetime.now(UTC)


def _fail(rec_id: uuid.UUID, message: str, requeue: bool = False) -> None:
    with session_scope() as db:
        db.execute(
            update(Recording)
            .where(Recording.id == rec_id)
            .values(
                status=STATUS_QUEUED if requeue else STATUS_ERROR,
                error=None if requeue else message[:2000],
                progress=0.0,
                # A clean shutdown shouldn't count against the retry budget.
                attempts=Recording.attempts - (1 if requeue else 0),
            )
        )


def disable_library_telemetry() -> None:
    """Transcription is meant to be fully offline and private: opt out of
    onnxruntime's usage telemetry (used by faster-whisper's VAD)."""
    try:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
    except Exception as exc:  # optional dependency in dev/test environments
        log.debug("Could not disable onnxruntime telemetry: %s", exc)


def run_job(rec_id: uuid.UUID) -> None:
    """Process one claimed recording, turning failures into user-facing errors."""
    try:
        process(rec_id)
    except Cancelled:
        log.info("[%s] recording deleted mid-job; skipping.", rec_id)
        media.remove_recording_media(rec_id)
    except InterruptedError:
        log.info("[%s] interrupted by shutdown; re-queued.", rec_id)
        _fail(rec_id, "", requeue=True)
    except model_store.ModelNotDownloaded as exc:
        log.warning("[%s] model %s is not downloaded", rec_id, exc)
        label = stt_models.model_info(str(exc))["label"]
        _fail(rec_id, f"The “{label}” model ({exc}) isn't downloaded. Download it from the model list, then retry.")
    except media.MediaError as exc:
        log.warning("[%s] media error: %s", rec_id, exc)
        _fail(rec_id, f"Couldn't read this audio file. {exc}")
    except Exception as exc:
        log.exception("[%s] transcription failed", rec_id)
        _fail(rec_id, f"Transcription failed: {exc}")


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    # Hugging Face libraries log every HTTP request at INFO; keep the worker log readable.
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)  # "no HF_TOKEN" nag
    disable_library_telemetry()
    for _ in range(30):  # the DB may still be migrating on first start
        try:
            model_store.scan_models()
            break
        except Exception as exc:
            log.info("Waiting for database before scanning models: %s", exc)
            time.sleep(2)
    stop_downloads = threading.Event()
    threading.Thread(target=model_store.download_loop, args=(stop_downloads,), name="downloads", daemon=True).start()
    log.info("Worker started (models=%s, default=%s).", ",".join(settings.whisper_models), settings.whisper_model)

    while not _stop:
        try:
            rec_id = claim_job()
        except Exception:
            log.exception("Could not poll for jobs; retrying.")
            time.sleep(max(5, settings.worker_poll_seconds))
            continue
        if rec_id is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        run_job(rec_id)
    stop_downloads.set()
    log.info("Worker stopped.")


if __name__ == "__main__":
    run()
