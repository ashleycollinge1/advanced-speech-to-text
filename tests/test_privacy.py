"""Guards for the "offline and private" guarantees baked into the image."""

import re
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()


def _env(name):
    m = re.search(rf"^\s*{name}=(\S+?)\s*\\?$", DOCKERFILE, re.M)
    return m.group(1) if m else None


def test_onnxruntime_telemetry_disabled():
    # Without this, onnxruntime 1.32+ connects to mobile.events.data.microsoft.com during transcription.
    assert _env("ORT_DISABLE_TELEMETRY") == "1"


def test_huggingface_telemetry_and_xet_disabled():
    assert _env("HF_HUB_DISABLE_TELEMETRY") == "1"
    assert _env("HF_HUB_DISABLE_XET") == "1"  # plain HTTP downloads -> measurable progress


def test_worker_also_disables_onnxruntime_telemetry_at_runtime(monkeypatch):
    import sys
    import types

    from app import worker

    calls = []
    monkeypatch.setitem(
        sys.modules, "onnxruntime", types.SimpleNamespace(disable_telemetry_events=lambda: calls.append(1))
    )
    worker.disable_library_telemetry()
    assert calls == [1]
