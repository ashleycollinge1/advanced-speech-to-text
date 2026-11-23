"""Catalogue of transcription models offered to users.

Which models are offered is configured with WHISPER_MODELS. This file only
supplies friendly labels; unknown model names still work and are shown as-is.
"""

from __future__ import annotations

from .config import settings

# Ordered fastest → most accurate. Sizes are approximate download sizes.
KNOWN: dict[str, dict] = {
    "tiny.en": {"label": "Draft", "speed": "Fastest", "accuracy": 1, "size": "75 MB",
                "description": "Rough draft quality. Only useful for very clear audio."},
    "base.en": {"label": "Draft+", "speed": "Very fast", "accuracy": 2, "size": "140 MB",
                "description": "Quick, but noticeably error-prone."},
    "small.en": {"label": "Fast", "speed": "Fast", "accuracy": 3, "size": "460 MB",
                 "description": "Quick turnaround. Good on clear speech, weaker on accents, jargon and crosstalk."},
    "distil-small.en": {"label": "Fast (distilled)", "speed": "Fast", "accuracy": 3, "size": "320 MB",
                        "description": "Faster small model with slightly lower accuracy."},
    "medium.en": {"label": "Balanced", "speed": "Moderate", "accuracy": 4, "size": "1.4 GB",
                  "description": "Solid accuracy at a moderate speed."},
    "distil-large-v3": {"label": "Accurate (distilled)", "speed": "Moderate", "accuracy": 4, "size": "1.5 GB",
                        "description": "Close to large-v3 quality on long-form English, faster."},
    "large-v3-turbo": {"label": "Accurate", "speed": "Slower", "accuracy": 5, "size": "1.6 GB",
                       "description": "Close to the best accuracy, and quicker than large-v3."},
    "large-v3": {"label": "Most accurate", "speed": "Slowest", "accuracy": 6, "size": "3.1 GB",
                 "description": "Best accuracy, especially for difficult audio, names and jargon. Takes the longest."},
}  # fmt: skip


def model_info(name: str) -> dict:
    info = KNOWN.get(name, {"label": name, "speed": "", "accuracy": 0, "size": "", "description": ""})
    return {"id": name, **info, "default": name == settings.whisper_model}


def available() -> list[dict]:
    return [model_info(m) for m in settings.whisper_models]


def resolve(requested: str | None) -> str:
    """The model to use for a job: the requested one if still allowed, else the default."""
    return requested if requested in settings.whisper_models else settings.whisper_model
