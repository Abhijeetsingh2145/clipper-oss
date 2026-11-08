import pytest

from clipper import captions
from clipper import hooktext as H


def test_hook_is_cleaned_short_and_safe():
    assert H.clean('"When mummy becomes papa\'s spokesperson 😭 #comedy"') == "When mummy becomes papa's spokesperson"
    assert len(H.clean(" ".join(["word"] * 20)).split()) == 12
    assert H.clean("This is a scam", banned=["scam"]) == ""


def test_falls_back_to_the_post_title_when_the_model_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(H, "ask_json", boom)
    import clipper.llm
    monkeypatch.setattr(clipper.llm, "answered_by_fallback", lambda: False)
    assert H.write_hook("some words", "comedy", {"banned_words": []}, None, fallback="Papa vs mummy") == "Papa vs mummy"


def test_hook_stays_on_screen_for_the_whole_clip():
    ass = captions.build_ass([], {"enabled": False}, 42.5, (1080, 1920), {"text": "POV: dad sees the report card", "show": True, "seconds": 0})
    line = next(l for l in ass.splitlines() if l.startswith("Dialogue: 1"))
    assert ",0:00:00.00,0:00:42.50,Title," in line and "POV: dad sees the report card" in line


def test_a_hook_that_just_copies_the_transcript_is_rejected():
    said = "aapne bola hoga to main kaise bolunga yaar ghar pe sab sun rahe the"
    assert H.copies_transcript("Aapne bola hoga to main kaise bolunga", said)
    assert not H.copies_transcript("When papa hears everything from the next room", said)
