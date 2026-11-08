import numpy as np
import pytest

from clipper import llm
from clipper.analysis import Analysis
from clipper.modes import apply_recipe, detect, laughs
from clipper.music import bar_lines, song_sections
from clipper.picker import _through_laugh, pick_clips


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_model(*a, **k):
        raise RuntimeError("offline")
    import clipper.picker
    import clipper.scoring
    for mod in (llm, clipper.picker, clipper.scoring):
        monkeypatch.setattr(mod, "ask_json", no_model)


def standup(dur=600.0):
    """Ten minutes of one voice: a line every ~4 s, a loud laugh after every 3rd line, few camera cuts."""
    n = int(dur * 10)
    energy = [-55.0] * n
    words, segs, laugh_spans, t, k = [], [], [], 2.0, 0
    while t < dur - 6:
        ws = [f"w{k}_{i}" for i in range(6)]
        for i, w in enumerate(ws):
            s = t + i * 0.35
            words.append({"w": w + ("." if i == 5 else ""), "s": round(s, 2), "e": round(s + 0.3, 2)})
            for j in range(int(s * 10), int((s + 0.3) * 10)):
                energy[j] = -22.0
        segs.append({"s": t, "e": round(t + 2.1, 2), "t": " ".join(ws)})
        t += 2.1
        if k % 3 == 2:                                  # audience laughs for 2 s
            laugh_spans.append((t + 0.2, t + 2.2))
            for j in range(int((t + 0.2) * 10), int((t + 2.2) * 10)):
                energy[j] = -24.0
            t += 2.6
        else:
            t += 0.5
        k += 1
    data = {"cuts": [60.0 * i for i in range(1, 10)], "motion": [0.01] * n, "luma": [0.4] * n, "detail": [1.0] * n,
            "energy": energy, "faces": [{"t": i / 5, "d": []} for i in range(int(dur * 5))],
            "beats": [], "onsets": [], "bpm": 0, "beat_strength": 0.05, "fps": 30}
    tr = {"words": words, "segments": segs, "language": "hi"}
    return Analysis(data, tr), tr, laugh_spans


def test_audience_laughter_is_heard_between_lines():
    an, tr, truth = standup()
    found = laughs(an)
    assert len(found) >= len(truth) * 0.9
    assert all(any(abs(a - ta) < 0.4 for ta, _ in truth) for a, _ in found)


def test_standup_is_detected_as_comedy_not_montage():
    an, tr, _ = standup()
    mode, why = detect(tr, an, 600.0, "set.mp4")
    assert mode == "comedy" and "laugh" in why


def test_file_names_and_signals_route_other_types():
    an, tr, _ = standup()
    assert detect(tr, an, 600.0, "Lex Fridman Podcast Episode 400.mp4")[0] == "podcast"
    assert detect(tr, an, 600.0, "DMZ Early Access Gameplay Trailer.mp4")[0] == "trailer"
    assert detect(tr, an, 600.0, "Artist - Song (Official Music Video).mp4")[0] == "music"


def test_comedy_clip_runs_on_through_the_laugh_instead_of_cutting_it_off():
    c = _through_laugh({"start": 100.0, "end": 130.0, "reason": "bit"}, [(130.3, 133.0)], hi=60, t1=600)
    assert c["end"] == 133.3 and "laugh" in c["reason"]


def test_comedy_job_never_becomes_a_montage(monkeypatch):
    an, tr, _ = standup()
    rules = {"num_clips": 2, "min_seconds": 20, "max_seconds": 60, "target_seconds": 0, "banned_words": []}
    settings = apply_recipe({"edit_style": "montage"}, "comedy")
    assert settings["audio"]["pause_max"] == 0.6 and settings["audio"]["remove_fillers"] is False
    clips = pick_clips(tr, rules, 600.0, None, True, None, an=an, settings=settings)
    assert clips and all(not c.get("segments") for c in clips)          # one continuous bit each


def music_track(dur=180.0, bpm=120):
    n = int(dur * 10)
    energy = [-20.0] * n
    for i in range(600, 900):                     # 60-90 s: the loud chorus
        energy[i] = -8.0
    beats = [round(k * 0.5, 3) for k in range(int(dur * 2))]
    segs = [{"s": 61.0, "e": 64.0, "t": "we are the ones tonight"}, {"s": 140.0, "e": 143.0, "t": "we are the ones tonight"},
            {"s": 20.0, "e": 23.0, "t": "walking down the empty street"}]
    data = {"cuts": [], "motion": [0.02] * n, "luma": [0.4] * n, "detail": [1.0] * n, "energy": energy,
            "faces": [], "beats": beats, "onsets": beats[::4], "bpm": bpm, "beat_strength": 0.6, "fps": 30}
    return Analysis(data, {"words": [], "segments": segs}), {"words": [], "segments": segs}


def test_song_clip_starts_at_the_chorus_on_a_bar_line():
    an, tr = music_track()
    bars = set(round(float(b), 3) for b in bar_lines(an))
    clips = song_sections(tr, an, {"min_seconds": 20, "max_seconds": 35, "target_seconds": 30}, 0, 180, 2)
    assert clips[0]["start"] in bars and clips[0]["end"] in bars
    assert 58 <= clips[0]["start"] <= 61 and 20 <= clips[0]["end"] - clips[0]["start"] <= 35


def test_funnier_bits_score_higher_and_laughless_bits_are_dropped():
    from clipper.modes import laughs as find_laughs
    from clipper.picker import _worth_watching
    from clipper.scoring import score_clip, video_stats
    an, tr, truth = standup()
    rules = {"min_seconds": 10, "max_seconds": 60, "banned_words": []}
    stats = {**video_stats(an), "laughs": find_laughs(an), "duration": 600.0}
    a0 = truth[10][0]
    funny = score_clip(a0 - 25, a0 + 2, an, rules, stats=stats, mode="comedy", use_llm=False)
    flat = score_clip(a0 + 2.5, a0 + 7.0, an, rules, stats=stats, mode="comedy", use_llm=False)   # between laughs
    assert funny["parts"]["content"]["score"] > flat["parts"]["content"]["score"]
    cands = [{"start": a0 - 25, "end": a0 + 2, "score": funny["total"], "score_parts": funny},
             {"start": a0 + 2.5, "end": a0 + 7.0, "score": 99, "score_parts": flat}]
    assert [c["start"] for c in _worth_watching(cands, 1, "comedy", stats)] == [a0 - 25]


def test_podcast_filler_scores_zero_and_unquoted_importance_does_not_count(monkeypatch):
    import clipper.scoring as sc
    an, tr, _ = standup()
    rules = {"min_seconds": 10, "max_seconds": 60, "banned_words": []}
    base = {"hook_bold_claim": False, "hook_conflict": False, "hook_curiosity_gap": False, "stands_alone": True,
            "stands_alone_reason": "", "payoff_type": "clear_point", "payoff_strength": 2, "payoff_quote": "",
            "fit_score": 5, "fit_reason": "", "funny": 0, "importance_reason": "big idea"}
    first = tr["segments"][0]["t"]
    monkeypatch.setattr(sc, "_rubric", lambda *a, **k: {**base, "importance": 3, "is_filler": True, "key_quote": first})
    assert sc.score_clip(2, 30, an, rules, model="x", mode="podcast")["parts"]["content"]["score"] == 0
    monkeypatch.setattr(sc, "_rubric", lambda *a, **k: {**base, "importance": 3, "is_filler": False, "key_quote": "never said this"})
    assert sc.score_clip(2, 30, an, rules, model="x", mode="podcast")["parts"]["content"]["score"] == 0
    monkeypatch.setattr(sc, "_rubric", lambda *a, **k: {**base, "importance": 3, "is_filler": False, "key_quote": first})
    assert sc.score_clip(2, 30, an, rules, model="x", mode="podcast")["parts"]["content"]["score"] == 100
