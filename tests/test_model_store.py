"""Model downloads: API, disk scanning, the download worker, and offline-only transcription."""

import os
import threading
import time
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import Api, mark_models_ready
from sqlalchemy import delete

from app import media, model_store, worker
from app.config import settings
from app.db import session_scope
from app.models import (
    MODEL_DOWNLOADING,
    MODEL_ERROR,
    MODEL_MISSING,
    MODEL_QUEUED,
    MODEL_READY,
    Recording,
    WhisperModelState,
)


def _set(name, **values):
    with session_scope() as db:
        row = db.get(WhisperModelState, name) or WhisperModelState(name=name, bytes_done=0)
        for k, v in values.items():
            setattr(row, k, v)
        db.add(row)


def _get(name):
    with session_scope() as db:
        row = db.get(WhisperModelState, name)
        return None if row is None else {c: getattr(row, c) for c in ("status", "bytes_done", "bytes_total", "error")}


def _clear_models():
    with session_scope() as db:
        db.execute(delete(WhisperModelState))


def _models(api):
    return {m["id"]: m for m in api.get("/api/models").json()["models"]}


@pytest.fixture()
def models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setattr(type(settings), "models_dir", property(lambda self: d), raising=False)
    return d


# =========================================================================== API


def test_models_report_missing_when_nothing_downloaded(api):
    _clear_models()
    res = api.get("/api/models").json()
    assert res["any_ready"] is False
    for m in res["models"]:
        assert m["status"] == "missing" and m["ready"] is False and m["bytes_done"] == 0


def test_models_report_ready_and_progress(api):
    _clear_models()
    mark_models_ready(["small.en"])
    _set("large-v3", status=MODEL_DOWNLOADING, bytes_done=500, bytes_total=2000)
    _set("medium.en", status=MODEL_ERROR, error="Download failed: boom")
    res = api.get("/api/models").json()
    m = {x["id"]: x for x in res["models"]}
    assert res["any_ready"] is True
    assert m["small.en"]["ready"] is True
    assert (m["large-v3"]["status"], m["large-v3"]["bytes_done"], m["large-v3"]["bytes_total"]) == (
        "downloading",
        500,
        2000,
    )
    assert m["medium.en"]["error"] == "Download failed: boom"
    assert m["large-v3-turbo"]["status"] == "missing"


def test_request_download_queues_and_is_idempotent(api):
    _clear_models()
    r = api.post("/api/models/large-v3/download")
    assert r.status_code == 202 and r.json()["model"]["status"] == "queued"
    assert api.post("/api/models/large-v3/download").json()["model"]["status"] == "queued"
    with session_scope() as db:
        assert db.query(WhisperModelState).filter_by(name="large-v3").count() == 1


def test_request_download_leaves_ready_and_downloading_alone(api):
    _set("large-v3", status=MODEL_DOWNLOADING, bytes_done=10, bytes_total=100)
    assert api.post("/api/models/large-v3/download").json()["model"]["status"] == "downloading"
    assert _get("large-v3")["bytes_done"] == 10
    assert api.post("/api/models/small.en/download").json()["model"]["status"] == "ready"


def test_request_download_retries_after_error(api):
    _set("medium.en", status=MODEL_ERROR, error="Download failed: network", bytes_done=5)
    r = api.post("/api/models/medium.en/download").json()["model"]
    assert r["status"] == "queued" and r["error"] is None and r["bytes_done"] == 0


def test_request_download_validation_and_auth(api, client):
    assert api.post("/api/models/not-a-model/download").status_code == 404
    assert api.post("/api/models/../etc/download").status_code == 404
    anon = Api(type(client)(client.app))
    assert anon.post("/api/models/small.en/download").status_code == 401
    # Signed in but missing CSRF token.
    assert client.post("/api/models/small.en/download").status_code == 403


def test_any_user_can_request_download(api, client):
    from fastapi.testclient import TestClient

    from app.main import app

    temp = api.post("/api/users", json={"username": "bob"}).json()["temporary_password"]
    bob = Api(TestClient(app))
    bob.login("bob", temp)
    bob.post("/api/auth/password", json={"current_password": temp, "new_password": "bob-strong-password"})
    _set("large-v3", status=MODEL_MISSING)
    assert bob.post("/api/models/large-v3/download").json()["model"]["status"] == "queued"


def test_upload_refused_for_model_not_downloaded(api):
    _set("large-v3", status=MODEL_MISSING)
    before = set(settings.media_dir.iterdir())
    r = api.post("/api/recordings?filename=x.wav&model=large-v3", content=b"RIFF")
    assert r.status_code == 409
    assert "isn't downloaded" in r.json()["detail"] and "Most accurate" in r.json()["detail"]
    assert set(settings.media_dir.iterdir()) == before  # nothing stored


@pytest.mark.parametrize("state", [MODEL_MISSING, MODEL_QUEUED, MODEL_DOWNLOADING, MODEL_ERROR])
def test_upload_refused_unless_ready(api, state):
    _set("small.en", status=state)
    assert api.post("/api/recordings?filename=x.wav&model=small.en", content=b"RIFF").status_code == 409


def test_upload_with_default_model_not_downloaded(api):
    _set(settings.whisper_model, status=MODEL_MISSING)
    assert api.post("/api/recordings?filename=x.wav", content=b"RIFF").status_code == 409


def test_retranscribe_refused_for_model_not_downloaded(api):
    rid = api.post("/api/recordings?filename=x.wav&model=small.en", content=b"RIFF").json()["recording"]["id"]
    worker.claim_job()
    worker.save_result(uuid.UUID(rid), [], "small.en")
    _set("large-v3", status=MODEL_MISSING)
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "large-v3"})
    assert r.status_code == 409
    rec = api.get(f"/api/recordings/{rid}").json()["recording"]
    assert rec["status"] == "done" and rec["model"] == "small.en"


# =========================================================================== disk helpers


def test_dir_size_ignores_symlinks_and_model_size_follows_them(tmp_path):
    blobs = tmp_path / "blobs"
    snap = tmp_path / "snapshots" / "abc"
    blobs.mkdir()
    snap.mkdir(parents=True)
    (blobs / "x").write_bytes(b"0" * 100)
    os.symlink(blobs / "x", snap / "model.bin")
    assert model_store.dir_size(tmp_path) == 100  # blob counted once, symlink ignored
    assert model_store.model_size(snap) == 100  # snapshot size via the symlink


# =========================================================================== scan


def test_scan_marks_present_ready_and_absent_missing(client, models_dir, monkeypatch):
    _clear_models()
    snap = models_dir / "small"
    snap.mkdir()
    (snap / "model.bin").write_bytes(b"0" * 1234)
    monkeypatch.setattr(model_store, "_local_path", lambda n: str(snap) if n == "small.en" else None)
    result = model_store.scan_models()
    assert result["small.en"] == MODEL_READY
    assert _get("small.en") == {"status": MODEL_READY, "bytes_done": 1234, "bytes_total": 1234, "error": None}
    assert {result[n] for n in settings.whisper_models if n != "small.en"} == {MODEL_MISSING}


def test_scan_resumes_interrupted_downloads_and_detects_deleted_models(client, models_dir, monkeypatch):
    _set("large-v3", status=MODEL_DOWNLOADING, bytes_done=50)
    _set("medium.en", status=MODEL_READY, bytes_done=99)
    _set("small.en", status=MODEL_QUEUED)
    monkeypatch.setattr(model_store, "_local_path", lambda n: None)
    result = model_store.scan_models()
    assert result["large-v3"] == MODEL_QUEUED  # resume after restart
    assert result["medium.en"] == MODEL_MISSING and _get("medium.en")["bytes_done"] == 0
    assert result["small.en"] == MODEL_QUEUED  # untouched


def test_scan_never_downloads(client, models_dir, monkeypatch):
    monkeypatch.setattr(model_store, "_local_path", lambda n: None)
    monkeypatch.setattr(model_store, "_download", lambda n: pytest.fail("scan must not download"))
    monkeypatch.setattr(model_store, "_expected_size", lambda n: pytest.fail("scan must not go online"))
    model_store.scan_models()


# =========================================================================== claiming


def test_claim_download_picks_queued_only(client):
    _clear_models()
    assert model_store.claim_download() is None
    _set("medium.en", status=MODEL_QUEUED)
    assert model_store.claim_download() == "medium.en"
    assert _get("medium.en")["status"] == MODEL_DOWNLOADING
    assert model_store.claim_download() is None  # fresh heartbeat: not reclaimed


def test_claim_download_reclaims_stale(client):
    _set("medium.en", status=MODEL_DOWNLOADING, heartbeat_at=datetime.now(UTC) - timedelta(minutes=10))
    assert model_store.claim_download() == "medium.en"


def test_claim_download_ignores_unconfigured_models(client):
    _clear_models()
    _set("tiny.en", status=MODEL_QUEUED)  # not in WHISPER_MODELS
    assert model_store.claim_download() is None


# =========================================================================== run_download


@pytest.fixture()
def fake_hub(models_dir, monkeypatch):
    """A fake Hugging Face: downloads write chunks into models_dir."""
    state = {"present": set(), "calls": [], "chunks": 3, "chunk": 1000, "fail": None, "incomplete": False}

    def local_path(name):
        return str(models_dir / name) if name in state["present"] else None

    def download(name):
        state["calls"].append(name)
        target = models_dir / name
        target.mkdir(exist_ok=True)
        for i in range(state["chunks"]):
            if state["fail"] and i == 1:
                raise RuntimeError(state["fail"])
            with open(target / "model.bin", "ab") as fh:
                fh.write(b"0" * state["chunk"])
            time.sleep(0.08)
        if not state["incomplete"]:
            state["present"].add(name)
        return str(target)

    monkeypatch.setattr(model_store, "_local_path", local_path)
    monkeypatch.setattr(model_store, "_download", download)
    monkeypatch.setattr(model_store, "_expected_size", lambda n: state["chunks"] * state["chunk"])
    return state


def test_run_download_success_reports_progress(client, fake_hub, monkeypatch):
    _set("large-v3", status=MODEL_DOWNLOADING)
    seen = []
    real_update = model_store._update
    monkeypatch.setattr(
        model_store, "_update", lambda n, **v: (seen.append(v.get("bytes_done")), real_update(n, **v))[1]
    )
    assert model_store.run_download("large-v3", poll_seconds=0.03) is True
    final = _get("large-v3")
    assert final == {"status": MODEL_READY, "bytes_done": 3000, "bytes_total": 3000, "error": None}
    progress = [b for b in seen if b is not None]
    assert any(0 < b < 3000 for b in progress), progress  # intermediate progress was reported
    assert progress == sorted(progress)  # never goes backwards
    assert fake_hub["calls"] == ["large-v3"]


def test_run_download_failure_records_error(client, fake_hub):
    fake_hub["fail"] = "connection reset"
    _set("large-v3", status=MODEL_DOWNLOADING)
    assert model_store.run_download("large-v3", poll_seconds=0.03) is False
    final = _get("large-v3")
    assert final["status"] == MODEL_ERROR and "connection reset" in final["error"] and final["bytes_done"] == 0


def test_run_download_incomplete_files_is_an_error(client, fake_hub):
    fake_hub["incomplete"] = True
    _set("large-v3", status=MODEL_DOWNLOADING)
    assert model_store.run_download("large-v3", poll_seconds=0.03) is False
    assert "incomplete" in _get("large-v3")["error"]


def test_run_download_checks_disk_space_first(client, fake_hub, monkeypatch):
    monkeypatch.setattr(model_store.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=10))
    _set("large-v3", status=MODEL_DOWNLOADING)
    assert model_store.run_download("large-v3") is False
    assert "Not enough disk space" in _get("large-v3")["error"]
    assert fake_hub["calls"] == []


def test_run_download_skips_when_already_on_disk(client, fake_hub):
    fake_hub["present"].add("large-v3")
    _set("large-v3", status=MODEL_DOWNLOADING)
    assert model_store.run_download("large-v3") is True
    assert _get("large-v3")["status"] == MODEL_READY and fake_hub["calls"] == []


def test_run_download_with_unknown_size(client, fake_hub, monkeypatch):
    monkeypatch.setattr(model_store, "_expected_size", lambda n: None)
    _set("large-v3", status=MODEL_DOWNLOADING)
    assert model_store.run_download("large-v3", poll_seconds=0.03) is True
    assert _get("large-v3")["bytes_total"] == 3000  # measured from disk


def test_download_loop_processes_requests_end_to_end(api, fake_hub):
    _set("large-v3", status=MODEL_MISSING)
    api.post("/api/models/large-v3/download")
    stop = threading.Event()
    t = threading.Thread(target=model_store.download_loop, args=(stop, 0.05), daemon=True)
    t.start()
    for _ in range(100):
        if _models(api)["large-v3"]["ready"]:
            break
        time.sleep(0.05)
    stop.set()
    t.join(2)
    assert _models(api)["large-v3"]["ready"] is True
    assert not t.is_alive()
    # ...and now uploads with it are accepted.
    assert api.post("/api/recordings?filename=x.wav&model=large-v3", content=b"RIFF").status_code == 201


# =========================================================================== offline transcription


def test_get_model_refuses_when_not_on_disk(client, monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail("must not load/download")),
    )
    monkeypatch.setattr(model_store, "_local_path", lambda n: None)
    monkeypatch.setattr(worker, "_model", None)
    monkeypatch.setattr(worker, "_model_name", None)
    with pytest.raises(model_store.ModelNotDownloaded):
        worker.get_model("medium.en")
    assert _get("medium.en")["status"] == MODEL_MISSING  # UI now offers to download it again


def test_job_with_deleted_model_fails_with_helpful_message(api, monkeypatch):
    rid = api.post("/api/recordings?filename=x.wav&model=medium.en", content=b"RIFF").json()["recording"]["id"]
    monkeypatch.setattr(media, "make_playback", lambda src, dst: dst.write_bytes(b"m4a"))
    monkeypatch.setattr(media, "probe_duration", lambda p: 1.0)
    monkeypatch.setattr(model_store, "_local_path", lambda n: None)  # files removed after upload
    monkeypatch.setattr(worker, "_model", None)
    monkeypatch.setattr(worker, "_model_name", None)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", types.SimpleNamespace(WhisperModel=object))
    assert worker.claim_job() == uuid.UUID(rid)
    worker.run_job(uuid.UUID(rid))
    with session_scope() as db:
        rec = db.get(Recording, uuid.UUID(rid))
        assert rec.status == "error"
        assert "Balanced" in rec.error and "isn't downloaded" in rec.error and "Download it" in rec.error
