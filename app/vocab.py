"""Per-recording vocabulary hints ("hotwords") for the transcriber.

Users type names, acronyms and jargon. We normalise that into a single
comma-separated string which the worker passes to faster-whisper as
``hotwords``. Unlike ``initial_prompt``, hotwords are included in the prompt
for every 30s window, so they help across a whole long recording.

faster-whisper silently truncates hotwords beyond max_length // 2 - 1 = 223
tokens. Two layers keep us inside that:
  * ``normalize_vocabulary`` enforces a conservative character/term limit
    (gives the user a clear error at upload time);
  * ``fit_hotwords`` checks the real token count in the worker and drops
    whole terms from the end if needed, rather than cutting mid-word.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

MAX_CHARS = 400  # after normalisation; ~2 chars/token worst case for unusual names → < 223 tokens
MAX_TERMS = 100
MAX_TERM_CHARS = 60
TOKEN_BUDGET = 223  # faster-whisper: max_length (448) // 2 - 1

_SPLIT_RE = re.compile(r"[,;\n\r\t]+")
_SPACE_RE = re.compile(r"\s+")


class VocabularyError(ValueError):
    pass


def _clean_term(term: str) -> str:
    # Drop control/format characters (zero-width etc.), collapse whitespace.
    term = "".join(ch for ch in term if unicodedata.category(ch)[0] != "C" or ch == " ")
    return _SPACE_RE.sub(" ", term).strip(" .")


def split_terms(raw: str | None) -> list[str]:
    """Split user input on commas, semicolons, tabs and new lines; clean and
    de-duplicate (case-insensitively, keeping the first spelling given)."""
    if not raw:
        return []
    seen: set[str] = set()
    terms: list[str] = []
    for part in _SPLIT_RE.split(raw):
        term = _clean_term(part)
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def normalize_vocabulary(raw: str | None) -> str | None:
    """Return the canonical stored form ("Term A, Term B") or None if empty.

    Raises VocabularyError with a user-facing message when the input is too long.
    """
    terms = split_terms(raw)
    if not terms:
        return None
    too_long = [t for t in terms if len(t) > MAX_TERM_CHARS]
    if too_long:
        raise VocabularyError(
            f"Each hint should be a word or short phrase (max {MAX_TERM_CHARS} characters). "
            f"Separate terms with commas. Too long: “{too_long[0][:30]}…”"
        )
    if len(terms) > MAX_TERMS:
        raise VocabularyError(f"Too many hints ({len(terms)}). Keep it to the {MAX_TERMS} most important terms.")
    joined = ", ".join(terms)
    if len(joined) > MAX_CHARS:
        raise VocabularyError(
            f"Hints are too long ({len(joined)}/{MAX_CHARS} characters). "
            "Keep only names and terms that actually appear in this recording."
        )
    return joined


def fit_hotwords(vocabulary: str | None, encode: Callable[[str], list[int]] | None) -> tuple[str | None, list[str]]:
    """Format hints for the model and trim whole terms until they fit the token budget.

    The result ends with a full stop: Whisper imitates the style of its prompt,
    and an unpunctuated hint list measurably makes it drop punctuation from the
    transcript ("cloud based application" instead of "cloud-based application.").

    ``encode`` is the model's tokenizer (text → token ids); faster-whisper
    encodes hotwords as " " + text, so we do the same. Returns the hotwords to
    use and the list of terms that had to be dropped.
    """
    terms = split_terms(vocabulary)

    def fmt(ts: list[str]) -> str:
        return ", ".join(ts) + "."

    if not terms:
        return None, []
    if encode is None:
        return fmt(terms), []
    dropped: list[str] = []
    while terms and len(encode(" " + fmt(terms))) > TOKEN_BUDGET:
        dropped.insert(0, terms.pop())
    return (fmt(terms) if terms else None), dropped
