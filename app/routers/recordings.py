from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import media, stt_models
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import STATUS_DONE, STATUS_ERROR, STATUS_QUEUED, Highlight, Recording, Segment, User
from ..security import current_user
from ..transcript import realign_words, to_srt, to_txt, to_vtt
from ..vocab import MAX_CHARS, VocabularyError, normalize_vocabulary

router = APIRouter(prefix="/api", tags=["recordings"])


# --------------------------------------------------------------------------- helpers


def recording_out(rec: Recording, viewer: User) -> dict:
    return {
        "id": str(rec.id),
        "title": rec.title,
        "original_filename": rec.original_filename,
        "source": rec.source,
        "size_bytes": rec.size_bytes,
        "status": rec.status,
        "progress": round(rec.progress or 0.0, 4),
        "error": rec.error,
        "duration": rec.duration,
        "model": rec.model,
        "vocabulary": rec.vocabulary,
        "has_audio": rec.has_playback,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
        "owner": rec.owner.username if rec.owner else None,
        "is_mine": rec.owner_id == viewer.id,
    }


def highlight_out(h: Highlight) -> dict:
    return {
        "id": h.id,
        "recording_id": str(h.recording_id),
        "start": h.start,
        "end": h.end,
        "text": h.text,
        "note": h.note,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def get_recording(db: Session, recording_id: uuid.UUID, user: User) -> Recording:
    rec = db.get(Recording, recording_id)
    # 404 (not 403) for other users' recordings so IDs can't be probed.
    if rec is None or (rec.owner_id != user.id and not user.is_admin):
        raise HTTPException(404, "Recording not found.")
    return rec


def _download_name(title: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip().replace(" ", "_")[:80] or "transcript"
    return f"{base}.{ext}"


def _check_vocabulary(raw: str | None) -> str | None:
    try:
        return normalize_vocabulary(raw)
    except VocabularyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _check_model(model: str | None) -> str:
    if model is None or model == "":
        return settings.whisper_model
    if model not in settings.whisper_models:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown model {model!r}.")
    return model


# --------------------------------------------------------------------------- models


@router.get("/models")
def list_models(_: User = Depends(current_user)) -> dict:
    return {"models": stt_models.available(), "default": settings.whisper_model}


# --------------------------------------------------------------------------- recordings


@router.get("/recordings")
def list_recordings(
    scope: str = Query("mine", pattern="^(mine|all)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Recording).order_by(Recording.created_at.desc())
    if scope == "mine" or not user.is_admin:
        stmt = stmt.where(Recording.owner_id == user.id)
    recs = db.scalars(stmt).all()
    return {"recordings": [recording_out(r, user) for r in recs]}


@router.post("/recordings", status_code=201)
async def upload_recording(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    title: str | None = Query(None, max_length=255),
    source: str = Query("upload", pattern="^(upload|browser)$"),
    model: str | None = Query(None, max_length=64),
    vocabulary: str | None = Query(None, max_length=MAX_CHARS * 4),
    user: User = Depends(current_user),
) -> dict:
    """Raw-body upload (the request body is the file itself). It streams straight
    to disk, so large files never sit in memory or a temp spool."""
    model = _check_model(model)
    vocabulary = _check_vocabulary(vocabulary)  # validate before accepting a large body
    ext = media.safe_ext(filename)
    if ext is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported file type.")
    limit = settings.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {settings.max_upload_mb} MB limit.")

    rec_id = uuid.uuid4()
    rec_dir = media.recording_dir(rec_id)
    rec_dir.mkdir(parents=True, exist_ok=False)
    part = rec_dir / f"original.{ext}.part"
    written = 0
    try:
        with part.open("wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {settings.max_upload_mb} MB limit."
                    )
                fh.write(chunk)
        if written == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")
        part.replace(media.original_path(rec_id, ext))
    except BaseException:
        media.remove_recording_media(rec_id)
        raise

    clean_name = os.path.basename(filename)[:255]
    clean_title = (title or "").strip() or Path(clean_name).stem or "Untitled recording"

    def _save() -> dict:
        with SessionLocal() as db:
            rec = Recording(
                id=rec_id,
                owner_id=user.id,
                title=clean_title[:255],
                original_filename=clean_name,
                original_ext=ext,
                size_bytes=written,
                source=source,
                status=STATUS_QUEUED,
                model=model,
                vocabulary=vocabulary,
            )
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return recording_out(rec, user)

    try:
        return {"recording": await run_in_threadpool(_save)}
    except Exception:
        media.remove_recording_media(rec_id)
        raise


@router.get("/recordings/{recording_id}")
def get_one(recording_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"recording": recording_out(get_recording(db, recording_id, user), user)}


class RecordingPatchIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@router.patch("/recordings/{recording_id}")
def rename(
    recording_id: uuid.UUID, body: RecordingPatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    rec = get_recording(db, recording_id, user)
    rec.title = body.title.strip() or rec.title
    db.commit()
    return {"recording": recording_out(rec, user)}


@router.delete("/recordings/{recording_id}")
def delete_recording(
    recording_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    rec = get_recording(db, recording_id, user)
    db.delete(rec)
    db.commit()
    media.remove_recording_media(recording_id)
    return {"ok": True}


@router.post("/recordings/{recording_id}/retry")
def retry(recording_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rec = get_recording(db, recording_id, user)
    if rec.status != STATUS_ERROR:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed recordings can be retried.")
    rec.status = STATUS_QUEUED
    rec.error = None
    rec.progress = 0.0
    rec.attempts = 0
    db.commit()
    return {"recording": recording_out(rec, user)}


class RetranscribeIn(BaseModel):
    model: str = Field(min_length=1, max_length=64)
    # Omitted → keep the recording's current hints; "" or null → clear them.
    vocabulary: str | None = Field(default=None, max_length=MAX_CHARS * 4)


@router.post("/recordings/{recording_id}/retranscribe")
def retranscribe(
    recording_id: uuid.UUID, body: RetranscribeIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """Queue a finished (or failed) recording to be transcribed again, e.g. with a
    more accurate model. Highlights are time-anchored so they survive; manual
    text edits are replaced when the new transcript is saved."""
    rec = get_recording(db, recording_id, user)
    if rec.status not in (STATUS_DONE, STATUS_ERROR):
        raise HTTPException(status.HTTP_409_CONFLICT, "This recording is already being transcribed.")
    rec.model = _check_model(body.model)
    if "vocabulary" in body.model_fields_set:
        rec.vocabulary = _check_vocabulary(body.vocabulary)
    rec.status = STATUS_QUEUED
    rec.error = None
    rec.progress = 0.0
    rec.attempts = 0
    db.commit()
    return {"recording": recording_out(rec, user)}


# --------------------------------------------------------------------------- transcript


def segment_out(s: Segment) -> dict:
    return {
        "id": s.id,
        "idx": s.idx,
        "start": s.start,
        "end": s.end,
        "text": s.text,
        "words": s.words,
        "edited": s.edited,
    }


@router.get("/recordings/{recording_id}/transcript")
def transcript(recording_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rec = get_recording(db, recording_id, user)
    segs = db.scalars(select(Segment).where(Segment.recording_id == rec.id).order_by(Segment.idx)).all()
    return {"segments": [segment_out(s) for s in segs]}


class SegmentEditIn(BaseModel):
    text: str = Field(max_length=20000)


@router.put("/recordings/{recording_id}/segments/{segment_id}")
def edit_segment(
    recording_id: uuid.UUID,
    segment_id: int,
    body: SegmentEditIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    rec = get_recording(db, recording_id, user)
    if rec.status != STATUS_DONE:
        raise HTTPException(status.HTTP_409_CONFLICT, "The transcript can only be edited after it finishes.")
    seg = db.get(Segment, segment_id)
    if seg is None or seg.recording_id != rec.id:
        raise HTTPException(404, "Segment not found.")
    text = " ".join(body.text.split())
    seg.words = realign_words(seg.words or [], text, seg.start, seg.end)
    seg.text = text
    seg.edited = True
    db.commit()
    db.refresh(seg)
    return {"segment": segment_out(seg)}


@router.get("/recordings/{recording_id}/audio")
def audio(recording_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = get_recording(db, recording_id, user)
    db.close()  # don't hold a pooled connection for the duration of the stream
    return media.range_response(request, media.playback_path(rec.id), media.PLAYBACK_MIME)


@router.get("/recordings/{recording_id}/export")
def export(
    recording_id: uuid.UUID,
    format: str = Query("txt", pattern="^(txt|srt|vtt)$"),
    timestamps: bool = True,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rec = get_recording(db, recording_id, user)
    rows = db.execute(
        select(Segment.start, Segment.end, Segment.text).where(Segment.recording_id == rec.id).order_by(Segment.idx)
    ).all()
    if format == "srt":
        body, mime = to_srt(rows), "application/x-subrip"
    elif format == "vtt":
        body, mime = to_vtt(rows), "text/vtt"
    else:
        body, mime = to_txt(rec.title, rows, timestamps), "text/plain"
    return PlainTextResponse(
        body,
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_download_name(rec.title, format)}"'},
    )


# --------------------------------------------------------------------------- highlights


class HighlightIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=5000)


class HighlightPatchIn(BaseModel):
    note: str = Field(max_length=5000)


def _get_highlight(db: Session, highlight_id: int, user: User) -> Highlight:
    h = db.get(Highlight, highlight_id)
    if h is None:
        raise HTTPException(404, "Highlight not found.")
    get_recording(db, h.recording_id, user)  # access check
    return h


@router.get("/recordings/{recording_id}/highlights")
def list_highlights(recording_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rec = get_recording(db, recording_id, user)
    hs = db.scalars(select(Highlight).where(Highlight.recording_id == rec.id).order_by(Highlight.start)).all()
    return {"highlights": [highlight_out(h) for h in hs]}


@router.post("/recordings/{recording_id}/highlights", status_code=201)
def create_highlight(
    recording_id: uuid.UUID, body: HighlightIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    rec = get_recording(db, recording_id, user)
    if body.end <= body.start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Highlight end must be after its start.")
    h = Highlight(
        recording_id=rec.id, user_id=user.id, start=body.start, end=body.end, text=body.text.strip(), note=body.note
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"highlight": highlight_out(h)}


@router.patch("/highlights/{highlight_id}")
def update_highlight(
    highlight_id: int, body: HighlightPatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    h = _get_highlight(db, highlight_id, user)
    h.note = body.note
    db.commit()
    return {"highlight": highlight_out(h)}


@router.delete("/highlights/{highlight_id}")
def delete_highlight(highlight_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    h = _get_highlight(db, highlight_id, user)
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.get("/highlights/{highlight_id}/clip")
def highlight_clip(highlight_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    h = _get_highlight(db, highlight_id, user)
    rec = db.get(Recording, h.recording_id)
    src = media.playback_path(rec.id)
    if not src.is_file():
        raise HTTPException(404, "Audio not available.")
    fd, tmp = tempfile.mkstemp(suffix=".m4a")
    os.close(fd)
    try:
        media.make_clip(src, h.start, h.end, Path(tmp))
    except media.MediaError as exc:
        os.unlink(tmp)
        raise HTTPException(500, "Could not create the clip.") from exc
    name = _download_name(f"{rec.title} clip {int(h.start)}s", "m4a")
    return FileResponse(tmp, media_type=media.PLAYBACK_MIME, filename=name, background=BackgroundTask(os.unlink, tmp))


# --------------------------------------------------------------------------- search


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    tsq = func.websearch_to_tsquery("english", q)
    # \x02/\x03 mark matches; the client splits on them, so no HTML is ever injected.
    headline = func.ts_headline(
        "english", Segment.text, tsq, "StartSel=\x02, StopSel=\x03, MaxFragments=2, MaxWords=30, MinWords=8"
    )
    stmt = (
        select(Segment.id, Segment.start, Recording.id, Recording.title, func.ts_rank(Segment.tsv, tsq), headline)
        .join(Recording, Recording.id == Segment.recording_id)
        .where(Segment.tsv.op("@@")(tsq))
        .order_by(func.ts_rank(Segment.tsv, tsq).desc(), Recording.created_at.desc(), Segment.idx)
        .limit(200)
    )
    if not user.is_admin:
        stmt = stmt.where(Recording.owner_id == user.id)
    rows = db.execute(stmt).all()

    groups: dict[str, dict] = {}
    for seg_id, start, rec_id, title, rank, snippet in rows:
        g = groups.setdefault(str(rec_id), {"recording_id": str(rec_id), "title": title, "score": 0.0, "hits": []})
        g["score"] = max(g["score"], float(rank))
        if len(g["hits"]) < 8:
            g["hits"].append({"segment_id": seg_id, "start": start, "snippet": snippet})
    results = sorted(groups.values(), key=lambda g: g["score"], reverse=True)
    return {"query": q, "results": results, "total_hits": len(rows)}
