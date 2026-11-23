"""Pure transcript helpers: word re-alignment after edits, and export formats."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher

Word = list  # [start: float, end: float, text: str]

_NORM_RE = re.compile(r"[^\w']+")


def _norm(token: str) -> str:
    return _NORM_RE.sub("", token.lower())


def _spread(tokens: Sequence[str], start: float, end: float) -> list[Word]:
    """Distribute [start, end] across tokens proportionally to their length."""
    if not tokens:
        return []
    end = max(end, start)
    weights = [max(len(t), 1) for t in tokens]
    total = sum(weights)
    out: list[Word] = []
    cursor = start
    for tok, w in zip(tokens, weights, strict=True):
        span = (end - start) * w / total
        out.append([round(cursor, 3), round(cursor + span, 3), tok])
        cursor += span
    out[-1][1] = round(end, 3)
    return out


def realign_words(old_words: Sequence[Word], new_text: str, seg_start: float, seg_end: float) -> list[Word]:
    """Give each token of an edited segment a timestamp, reusing the original
    word timings wherever the token still matches.

    Unchanged words keep their exact times. Replaced runs share the time span of
    the words they replaced. Inserted words take the gap between their neighbours.
    """
    tokens = new_text.split()
    if not tokens:
        return []
    if not old_words:
        return _spread(tokens, seg_start, seg_end)

    old_norm = [_norm(w[2]) for w in old_words]
    new_norm = [_norm(t) for t in tokens]
    out: list[Word] = []
    matcher = SequenceMatcher(a=old_norm, b=new_norm, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            # Same token count: keep each original timing (covers punctuation/case fixes too).
            for k in range(j2 - j1):
                ow = old_words[i1 + k]
                out.append([ow[0], ow[1], tokens[j1 + k]])
        elif op == "replace":
            out.extend(_spread(tokens[j1:j2], old_words[i1][0], old_words[i2 - 1][1]))
        elif op == "insert":
            left = old_words[i1 - 1][1] if i1 > 0 else seg_start
            right = old_words[i1][0] if i1 < len(old_words) else seg_end
            out.extend(_spread(tokens[j1:j2], left, right))
        # "delete": the words are gone, and so are their timings.
    return out


# --------------------------------------------------------------------------- export


def _ts(seconds: float, sep: str) -> str:
    ms_total = max(0, round(seconds * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def clock(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def to_srt(segments: Iterable[tuple[float, float, str]]) -> str:
    blocks = []
    n = 0
    for start, end, text in segments:
        if not text.strip():
            continue
        n += 1
        blocks.append(f"{n}\n{_ts(start, ',')} --> {_ts(end, ',')}\n{text.strip()}\n")
    return "\n".join(blocks)


def to_vtt(segments: Iterable[tuple[float, float, str]]) -> str:
    out = ["WEBVTT", ""]
    for start, end, text in segments:
        if not text.strip():
            continue
        out.append(f"{_ts(start, '.')} --> {_ts(end, '.')}")
        out.append(text.strip())
        out.append("")
    return "\n".join(out)


def to_txt(title: str, segments: Iterable[tuple[float, float, str]], timestamps: bool = True) -> str:
    lines = [title, "=" * len(title), ""]
    for start, _end, text in segments:
        if not text.strip():
            continue
        lines.append(f"[{clock(start)}] {text.strip()}" if timestamps else text.strip())
    return "\n".join(lines) + "\n"
