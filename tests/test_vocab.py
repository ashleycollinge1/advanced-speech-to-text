"""Unit tests for vocabulary hint parsing, validation and token fitting."""

import pytest

from app.vocab import (
    MAX_CHARS,
    MAX_TERM_CHARS,
    MAX_TERMS,
    TOKEN_BUDGET,
    VocabularyError,
    fit_hotwords,
    normalize_vocabulary,
    split_terms,
)

# --------------------------------------------------------------------------- split_terms


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Claude, Anthropic", ["Claude", "Anthropic"]),
        ("Claude;Anthropic\nKubernetes\tOKRs", ["Claude", "Anthropic", "Kubernetes", "OKRs"]),
        ("Claude,,, ,\n\nAnthropic", ["Claude", "Anthropic"]),
        ("  Siobhán   Ní   Bhriain  ", ["Siobhán Ní Bhriain"]),  # inner whitespace collapsed, accents kept
        ("Dr. Smith., Q3.", ["Dr. Smith", "Q3"]),  # trailing full stops trimmed
        ("Zero​Width, ctrl\x07char", ["ZeroWidth", "ctrlchar"]),  # control/format chars removed
        ("", []),
        (None, []),
        (" , ; \n ", []),
    ],
)
def test_split_terms(raw, expected):
    assert split_terms(raw) == expected


def test_split_terms_dedupes_case_insensitively_keeping_first_spelling():
    assert split_terms("iPhone, IPHONE, iphone, Anthropic, anthropic") == ["iPhone", "Anthropic"]


def test_split_terms_preserves_order():
    assert split_terms("c, a, b") == ["c", "a", "b"]


# --------------------------------------------------------------------------- normalize_vocabulary


def test_normalize_joins_with_comma_space():
    assert normalize_vocabulary("Claude\nAnthropic;  Kubernetes ") == "Claude, Anthropic, Kubernetes"


@pytest.mark.parametrize("raw", [None, "", "   ", ",,\n;"])
def test_normalize_empty_is_none(raw):
    assert normalize_vocabulary(raw) is None


def test_normalize_is_idempotent():
    once = normalize_vocabulary("b, A, a, c")
    assert normalize_vocabulary(once) == once == "b, A, c"


def test_normalize_rejects_long_term_with_helpful_message():
    with pytest.raises(VocabularyError, match="short phrase"):
        normalize_vocabulary("x" * (MAX_TERM_CHARS + 1))


def test_normalize_accepts_term_at_limit():
    assert normalize_vocabulary("x" * MAX_TERM_CHARS) == "x" * MAX_TERM_CHARS


def test_normalize_rejects_too_many_terms():
    raw = ", ".join(f"t{i}" for i in range(MAX_TERMS + 1))
    with pytest.raises(VocabularyError, match="Too many hints"):
        normalize_vocabulary(raw)


def test_normalize_rejects_total_length():
    terms = [f"term{i:03d}" + "x" * 40 for i in range(12)]  # 12 × ~47 chars > MAX_CHARS
    with pytest.raises(VocabularyError, match=f"/{MAX_CHARS} characters"):
        normalize_vocabulary(", ".join(terms))


def test_normalize_length_counted_after_dedupe():
    # Lots of duplicates in the raw input are fine: only the cleaned form counts.
    raw = ", ".join(["Anthropic"] * 500)
    assert len(raw) > MAX_CHARS
    assert normalize_vocabulary(raw) == "Anthropic"


# --------------------------------------------------------------------------- fit_hotwords


def word_tokens(text: str) -> list[int]:
    """Fake tokenizer: one token per whitespace-separated piece."""
    return list(range(len(text.split())))


def test_fit_hotwords_empty():
    assert fit_hotwords(None, word_tokens) == (None, [])
    assert fit_hotwords("", word_tokens) == (None, [])


def test_fit_hotwords_within_budget_unchanged():
    assert fit_hotwords("Claude, Anthropic", word_tokens) == ("Claude, Anthropic.", [])


def test_fit_hotwords_without_tokenizer_passes_through():
    assert fit_hotwords("Claude, Anthropic", None) == ("Claude, Anthropic.", [])


def test_fit_hotwords_drops_whole_terms_from_the_end():
    terms = [f"t{i}" for i in range(TOKEN_BUDGET + 5)]
    hotwords, dropped = fit_hotwords(", ".join(terms), word_tokens)
    assert hotwords.endswith(".")
    kept = hotwords[:-1].split(", ")
    assert len(word_tokens(" " + hotwords)) <= TOKEN_BUDGET
    assert kept == terms[: len(kept)]  # prefix kept, nothing cut mid-term
    assert dropped == terms[len(kept) :]  # dropped in original order
    assert kept + dropped == terms


def test_fit_hotwords_exactly_at_budget_is_kept():
    terms = [f"t{i}" for i in range(TOKEN_BUDGET)]
    hotwords, dropped = fit_hotwords(", ".join(terms), word_tokens)
    assert dropped == [] and len(hotwords.split(", ")) == TOKEN_BUDGET


def test_fit_hotwords_single_oversized_term_drops_everything():
    def huge(_text):
        return list(range(TOKEN_BUDGET + 1))

    assert fit_hotwords("Supercalifragilistic", huge) == (None, ["Supercalifragilistic"])


def test_fit_hotwords_encodes_with_leading_space_like_faster_whisper():
    seen = []
    fit_hotwords("Claude", lambda t: seen.append(t) or [1])
    assert seen == [" Claude."]


def test_fit_hotwords_always_ends_with_full_stop():
    # Whisper mirrors prompt style; an unpunctuated prompt strips punctuation from output.
    for raw in ["Claude", "Claude.", "Claude, Anthropic", "Dr. Smith"]:
        hotwords, _ = fit_hotwords(raw, None)
        assert hotwords.endswith(".") and not hotwords.endswith("..")


def test_fit_hotwords_budget_includes_full_stop():
    def char_tokens(text):  # one token per character, so the "." counts
        return list(text)

    terms = "a" * (TOKEN_BUDGET - 2)  # " " + terms = budget-1 tokens; "." makes it exactly the budget
    assert fit_hotwords(terms, char_tokens) == (terms + ".", [])
    assert fit_hotwords(terms + "a", char_tokens) == (None, [terms + "a"])
