import sys
import types
import uuid

from app import worker
from app.config import settings
from app.db import session_scope
from app.models import Recording


def _upload(a, model=None):
    url = "/api/recordings?filename=x.wav" + (f"&model={model}" if model else "")
    return a.post(url, content=b"RIFFdata")


def test_models_endpoint(api):
    res = api.get("/api/models").json()
    ids = [m["id"] for m in res["models"]]
    assert ids == ["small.en", "medium.en", "large-v3-turbo", "large-v3"]
    assert res["default"] == "large-v3-turbo"
    assert [m["id"] for m in res["models"] if m["default"]] == ["large-v3-turbo"]
    assert res["models"][-1]["label"] == "Most accurate"


def test_upload_records_chosen_model(api):
    assert _upload(api).json()["recording"]["model"] == "large-v3-turbo"
    assert _upload(api, "small.en").json()["recording"]["model"] == "small.en"
    bad = _upload(api, "huge-v9")
    assert bad.status_code == 422


def test_retranscribe(api):
    rid = _upload(api, "small.en").json()["recording"]["id"]
    # Still queued: can't re-run.
    assert api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "large-v3"}).status_code == 409
    worker.claim_job()
    worker.save_result(uuid.UUID(rid), [], "small.en")
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "large-v3"})
    assert r.status_code == 200
    rec = r.json()["recording"]
    assert rec["status"] == "queued" and rec["model"] == "large-v3"
    assert api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "nope"}).status_code == 409  # queued again
    assert worker.claim_job() == uuid.UUID(rid)
    with session_scope() as db:
        assert worker.stt_models.resolve(db.get(Recording, uuid.UUID(rid)).model) == "large-v3"


def test_retranscribe_rejects_unknown_model(api):
    rid = _upload(api).json()["recording"]["id"]
    worker.claim_job()
    worker.save_result(uuid.UUID(rid), [])
    assert api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "huge-v9"}).status_code == 422


def test_resolve_falls_back_when_model_removed():
    assert worker.stt_models.resolve("large-v3") == "large-v3"
    assert worker.stt_models.resolve("removed-model") == settings.whisper_model
    assert worker.stt_models.resolve(None) == settings.whisper_model


def test_worker_keeps_one_model_loaded(monkeypatch):
    loaded = []

    class FakeModel:
        def __init__(self, name, **kw):
            loaded.append(name)
            self.name = name

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(worker, "_model", None)
    monkeypatch.setattr(worker, "_model_name", None)
    a = worker.get_model("small.en")
    assert worker.get_model("small.en") is a  # cached
    b = worker.get_model("large-v3")
    assert b is not a and worker._model_name == "large-v3"
    assert loaded == ["small.en", "large-v3"]
