import pytest

from clipper import llm
from clipper.translit import needs_roman, romanize_words, rule_based


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_model(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "ask_json", no_model)


def test_rule_based_spelling_reads_like_hinglish():
    assert [rule_based(w) for w in "पूरे साल उसी के बात यार ना कहा".split()] == ["poore", "saal", "usi", "ke", "baat", "yaar", "na", "kaha"]
    assert rule_based("यार,") == "yaar,"                                   # punctuation kept
    assert rule_based("okay") == "okay"                                   # Latin words untouched


def test_every_word_keeps_its_own_timing_when_the_model_is_unreachable():
    words = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate("मेरा दोस्त कुणाल है।".split())]
    out = romanize_words(words)
    assert len(out) == len(words) and not needs_roman({"words": [{"w": x} for x in out]})


def test_model_answer_with_a_different_word_count_is_rejected(monkeypatch):
    import clipper.translit as T
    monkeypatch.setattr(T, "_model_lines", lambda lines, model: {0: ["mera dost kunal hai", "extra"]})
    words = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate("मेरा दोस्त कुणाल है।".split())]
    assert romanize_words(words) == [rule_based(w["w"]) for w in words]   # falls back, timings intact
    monkeypatch.setattr(T, "_model_lines", lambda lines, model: {0: ["mera", "dost", "Kunal", "hai."]})
    assert romanize_words(words) == ["mera", "dost", "Kunal", "hai."]
