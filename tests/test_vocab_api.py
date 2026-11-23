"""API and worker integration for vocabulary hints."""

import types
import uuid
from urllib.parse import quote

import pytest

from app import media, worker
from app.db import session_scope
from app.models import Recording
from app.vocab import MAX_CHARS, TOKEN_BUDGET


def _upload(api, vocabulary=None, model=None):
    url = "/api/recordings?filename=x.wav"
    if vocabulary is not None:
        url += f"&vocabulary={quote(vocabulary)}"
    if model:
        url += f"&model={model}"
    return api.post(url, content=b"RIFFdata")


def _finish(rid):
    worker.claim_job()
    worker.save_result(uuid.UUID(rid), [])


# --------------------------------------------------------------------------- upload


def test_upload_stores_normalised_vocabulary(api):
    r = _upload(api, "Claude\nAnthropic;  claude , Siobhán")
    assert r.status_code == 201, r.text
    rec = r.json()["recording"]
    assert rec["vocabulary"] == "Claude, Anthropic, Siobhán"
    assert api.get(f"/api/recordings/{rec['id']}").json()["recording"]["vocabulary"] == rec["vocabulary"]


def test_upload_without_vocabulary_is_null(api):
    assert _upload(api).json()["recording"]["vocabulary"] is None
    assert _upload(api, "  ,  ").json()["recording"]["vocabulary"] is None


def test_upload_rejects_oversized_vocabulary_and_writes_nothing(api):
    before = set(media.settings.media_dir.iterdir())
    r = _upload(api, ", ".join(f"term{i:03d}" + "x" * 40 for i in range(12)))
    assert r.status_code == 422
    assert f"/{MAX_CHARS} characters" in r.json()["detail"]
    assert set(media.settings.media_dir.iterdir()) == before  # rejected before the body is stored


def test_vocabulary_listed_in_recordings(api):
    _upload(api, "Kubernetes")
    assert api.get("/api/recordings").json()["recordings"][0]["vocabulary"] == "Kubernetes"


# --------------------------------------------------------------------------- re-transcribe


def test_retranscribe_replaces_vocabulary(api):
    rid = _upload(api, "Claud").json()["recording"]["id"]
    _finish(rid)
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "large-v3", "vocabulary": "Claude, Anthropic"})
    assert r.status_code == 200
    assert r.json()["recording"]["vocabulary"] == "Claude, Anthropic"


def test_retranscribe_without_field_keeps_vocabulary(api):
    rid = _upload(api, "Claude").json()["recording"]["id"]
    _finish(rid)
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "small.en"})
    assert r.json()["recording"]["vocabulary"] == "Claude"


@pytest.mark.parametrize("cleared", ["", None, " , "])
def test_retranscribe_can_clear_vocabulary(api, cleared):
    rid = _upload(api, "Claude").json()["recording"]["id"]
    _finish(rid)
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "small.en", "vocabulary": cleared})
    assert r.json()["recording"]["vocabulary"] is None


def test_retranscribe_rejects_bad_vocabulary_and_leaves_recording_unchanged(api):
    rid = _upload(api, "Claude").json()["recording"]["id"]
    _finish(rid)
    r = api.post(f"/api/recordings/{rid}/retranscribe", json={"model": "small.en", "vocabulary": "x" * 61})
    assert r.status_code == 422
    rec = api.get(f"/api/recordings/{rid}").json()["recording"]
    assert rec["status"] == "done" and rec["vocabulary"] == "Claude"


# --------------------------------------------------------------------------- worker


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return types.SimpleNamespace(ids=list(range(len(text.split()))))


class FakeModel:
    """Stands in for faster_whisper.WhisperModel; records transcribe() kwargs."""

    def __init__(self):
        self.calls = []
        self.hf_tokenizer = FakeTokenizer()

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        words = [types.SimpleNamespace(start=0.0, end=0.5, word=" Hello"),
                 types.SimpleNamespace(start=0.5, end=1.0, word=" Claude.")]  # fmt: skip
        seg = types.SimpleNamespace(start=0.0, end=1.0, text=" Hello Claude.", words=words)
        return iter([seg]), types.SimpleNamespace(duration=1.0)


@pytest.fixture()
def fake_pipeline(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(worker, "get_model", lambda name: model)
    monkeypatch.setattr(media, "make_playback", lambda src, dst: dst.write_bytes(b"m4a"))
    monkeypatch.setattr(media, "probe_duration", lambda path: 1.0)
    monkeypatch.setattr(media, "decode_pcm16k", lambda path: [0.0] * 16000)
    return model


def test_worker_passes_vocabulary_as_hotwords(api, fake_pipeline):
    rid = _upload(api, "Claude\nAnthropic").json()["recording"]["id"]
    assert worker.claim_job() == uuid.UUID(rid)
    worker.process(uuid.UUID(rid))
    (kwargs,) = fake_pipeline.calls
    assert kwargs["hotwords"] == "Claude, Anthropic."
    assert "initial_prompt" not in kwargs  # would be dropped after the first 30s window
    assert kwargs["condition_on_previous_text"] is False
    segs = api.get(f"/api/recordings/{rid}/transcript").json()["segments"]
    assert segs[0]["text"] == "Hello Claude."


def test_worker_without_vocabulary_passes_none(api, fake_pipeline):
    rid = _upload(api).json()["recording"]["id"]
    worker.claim_job()
    worker.process(uuid.UUID(rid))
    assert fake_pipeline.calls[0]["hotwords"] is None


def test_worker_trims_hotwords_to_token_budget(api, fake_pipeline):
    # Bypass API limits to simulate a tokenizer that's much hungrier than our estimate.
    rid = _upload(api, "Claude").json()["recording"]["id"]
    many = ", ".join(f"t{i}" for i in range(TOKEN_BUDGET + 20))
    with session_scope() as db:
        db.get(Recording, uuid.UUID(rid)).vocabulary = many
    worker.claim_job()
    worker.process(uuid.UUID(rid))
    hot = fake_pipeline.calls[0]["hotwords"]
    assert len(FakeTokenizer().encode(" " + hot).ids) <= TOKEN_BUDGET
    assert hot.endswith(".") and many.startswith(hot[:-1])


def test_hotwords_for_model_without_tokenizer():
    assert worker.hotwords_for(types.SimpleNamespace(), "Claude, Anthropic") == "Claude, Anthropic."
    assert worker.hotwords_for(types.SimpleNamespace(), None) is None
