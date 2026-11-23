from app.transcript import clock, realign_words, to_srt, to_txt, to_vtt

OLD = [[0.0, 0.5, "Hello"], [0.5, 1.0, "wurld,"], [1.2, 1.6, "how"], [1.6, 2.0, "are"], [2.0, 2.4, "you?"]]


def test_unchanged_keeps_times():
    assert realign_words(OLD, "Hello wurld, how are you?", 0, 2.4) == OLD


def test_punctuation_and_case_fix_keeps_times():
    out = realign_words(OLD, "hello world. How are you?", 0, 2.4)
    assert [w[0:2] for w in out] == [w[0:2] for w in OLD]
    assert [w[2] for w in out] == ["hello", "world.", "How", "are", "you?"]


def test_insert_uses_gap():
    out = realign_words(OLD, "Hello wurld, so how are you?", 0, 2.4)
    assert [w[2] for w in out] == ["Hello", "wurld,", "so", "how", "are", "you?"]
    so = out[2]
    assert 1.0 <= so[0] <= so[1] <= 1.2


def test_replace_run_spreads_over_old_span():
    out = realign_words(OLD, "Hello there my friend how are you?", 0, 2.4)
    mid = out[1:4]
    assert mid[0][0] == 0.5 and mid[-1][1] == 1.0
    assert all(a[1] <= b[0] + 1e-9 for a, b in zip(mid, mid[1:], strict=False))


def test_delete_drops_words():
    out = realign_words(OLD, "Hello you?", 0, 2.4)
    assert out == [[0.0, 0.5, "Hello"], [2.0, 2.4, "you?"]]


def test_empty_and_no_old_words():
    assert realign_words(OLD, "   ", 0, 2.4) == []
    out = realign_words([], "a bb", 1.0, 2.0)
    assert out[0][0] == 1.0 and out[-1][1] == 2.0


def test_exports():
    segs = [(0.0, 1.5, "Hello world."), (3661.25, 3662.0, "Later.")]
    srt = to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world.\n" in srt
    assert "01:01:01,250 --> 01:01:02,000" in srt
    vtt = to_vtt(segs)
    assert vtt.startswith("WEBVTT\n") and "01:01:01.250 --> 01:01:02.000" in vtt
    txt = to_txt("T", segs)
    assert "[1:01:01] Later." in txt
    assert clock(65) == "1:05"
