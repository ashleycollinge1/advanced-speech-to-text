import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import Api

from app import worker
from app.db import session_scope
from app.models import Recording, Segment


def _upload(client):
    a = Api(client)
    a.login()
    r = a.post("/api/recordings?filename=x.wav", content=b"RIFFdata")
    return a, uuid.UUID(r.json()["recording"]["id"])


def test_claim_save_and_reclaim(client):
    a, rid = _upload(client)
    assert worker.claim_job() == rid
    assert worker.claim_job() is None  # already processing, heartbeat fresh

    rows = [
        {"idx": 0, "start": 0.0, "end": 1.0, "text": "hello there", "words": [[0, 0.5, "hello"], [0.5, 1, "there"]]}
    ]
    worker.save_result(rid, rows)
    with session_scope() as db:
        rec = db.get(Recording, rid)
        assert rec.status == "done" and rec.progress == 1.0
        assert db.query(Segment).filter_by(recording_id=rid).count() == 1
    # Saving again replaces instead of duplicating.
    worker.save_result(rid, rows)
    with session_scope() as db:
        assert db.query(Segment).filter_by(recording_id=rid).count() == 1
    assert a.get("/api/search?q=hello").json()["results"]


def test_stale_job_is_reclaimed_and_gives_up(client):
    _, rid = _upload(client)
    assert worker.claim_job() == rid
    for _ in range(5):
        with session_scope() as db:
            db.get(Recording, rid).heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
        worker.claim_job()
    with session_scope() as db:
        rec = db.get(Recording, rid)
        assert rec.status == "error" and "Gave up" in rec.error


def test_requeue_on_shutdown_keeps_attempt_budget(client):
    _, rid = _upload(client)
    worker.claim_job()
    worker._fail(rid, "", requeue=True)
    with session_scope() as db:
        rec = db.get(Recording, rid)
        assert rec.status == "queued" and rec.attempts == 0


def test_save_after_delete_raises_cancelled(client):
    a, rid = _upload(client)
    worker.claim_job()
    a.delete(f"/api/recordings/{rid}")
    with pytest.raises(worker.Cancelled):
        worker.save_result(rid, [])


def test_merge_tokens():
    toks = [
        (0, 0.5, " Revenue"),
        (0.5, 0.8, " grew"),
        (0.8, 1.0, " 12"),
        (1.0, 1.1, "%"),
        (1.1, 1.2, " ok"),
        (1.2, 1.3, " "),
    ]
    assert worker.merge_tokens(toks) == [[0, 0.5, "Revenue"], [0.5, 0.8, "grew"], [0.8, 1.1, "12%"], [1.1, 1.2, "ok"]]
