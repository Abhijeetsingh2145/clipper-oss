import numpy as np
import pytest

from clipper import llm
from clipper import spec as S
from clipper.analysis import Analysis
from clipper.montage import OUT_FPS, build, cut_report, frame_safe

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Line ranking falls back to its measured heuristic: tests never call a model."""
    def no_model(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "ask_json", no_model)


RULES = {"min_seconds": 20, "max_seconds": 35, "num_clips": 2, "target_seconds": 0}
LINE = (74.5, 75.8)            # a line of dialogue that crosses the camera cut at 75.0


def trailer(dur=120.0, bpm=120):
    """Synthetic trailer: a cut every 1.5 s, steady beat, one loud action stretch, a fade to black at 40 s."""
    n = int(dur * 10)
    rng = np.random.default_rng(0)
    energy = list(-30 + rng.normal(0, 2, n))
    motion = list(0.02 + rng.random(n) * 0.01)
    for i in range(700, 900):                       # 70-90 s: the action
        energy[i] += 12
        motion[i] += 0.08
    luma = [0.3] * n
    for i in range(395, 425):                       # 39.5-42.5 s: black frames
        luma[i] = 0.0
        motion[i] = 0.3                             # flashes read as motion
    data = {"cuts": [round(1.5 * k, 2) for k in range(1, int(dur / 1.5))], "motion": motion, "luma": luma,
            "detail": [1.0] * n, "energy": energy, "faces": [{"t": i / 5, "d": []} for i in range(int(dur * 5))],
            "beats": [round(k * 60 / bpm, 3) for k in range(int(dur * bpm / 60))], "bpm": bpm, "beat_strength": 0.5,
            "onsets": [], "fps": 60}
    words = [{"w": w, "s": LINE[0] + 0.3 * i, "e": LINE[0] + 0.3 * i + 0.25} for i, w in enumerate("target is not planted".split())]
    return Analysis(data, {"words": words, "segments": [{"s": LINE[0], "e": LINE[1], "t": "target is not planted"}]})


def test_montage_takes_moments_from_across_the_video_without_sharing():
    an = trailer()
    clips = build(an, RULES, 0, 120, 2)
    assert len(clips) == 2
    for c in clips:
        assert len(c["segments"]) >= 4                                   # an edit, not one stretch
        total = sum(s["end"] - s["start"] for s in c["segments"])
        assert RULES["min_seconds"] - 1 <= total <= RULES["max_seconds"] + 1
    a, b = clips[0]["segments"], clips[1]["segments"]
    assert not any(x["start"] < y["end"] and y["start"] < x["end"] for x in a for y in b)


def test_montage_avoids_black_frames_and_prefers_action():
    c = build(trailer(), RULES, 0, 120, 1)[0]
    assert not any(s["start"] < 42.5 and 39.5 < s["end"] for s in c["segments"])
    assert sum(1 for s in c["segments"] if 70 <= s["start"] < 90) >= 3


def test_every_join_is_exactly_on_a_real_camera_cut():
    an = trailer()
    cuts = set(an.d["cuts"]) | {0.0, 120.0}
    for c in build(an, RULES, 0, 120, 2):
        for m in c["moments"]:
            assert m["start"] in cuts and m["end"] in cuts                # whole shots only
        r = cut_report(c, an)
        assert r["clean"] == r["joins"] > 0 and r["chopped"] == 0


def test_never_cuts_inside_a_line_of_dialogue():
    an = trailer()
    for c in build(an, RULES, 0, 120, 2):
        for m in c["moments"]:
            for t in (m["start"], m["end"]):
                assert not LINE[0] < t < LINE[1]


def test_segment_edges_stay_inside_the_shot():
    seg = frame_safe({"start": 10.0, "end": 12.0, "start_kind": "cut", "end_kind": "cut"}, 60)
    assert 10.0 - 1 / 60 < seg["start"] <= 10.0                          # seek lands on the shot's first frame
    assert abs(seg["end"] - (12.0 - 1 / OUT_FPS)) < 1e-3                  # last output frame before the next shot


def test_montage_captions_stay_with_their_shot():
    sp = S.new_spec(segments=[{"start": 80, "end": 82}, {"start": 10, "end": 12}],
                    words=[{"w": "late", "s": 81.0, "e": 81.4}, {"w": "early", "s": 10.5, "e": 10.9},
                           {"w": "elsewhere", "s": 50.0, "e": 50.3}])
    assert S.duration(sp) == 4
    assert [(w["w"], w["s"]) for w in S.output_words(sp)] == [("late", 1.0), ("early", 2.5)]


def test_old_specs_drop_the_separate_soundtrack():
    assert "audio_bed" not in S.normalize({"segments": [], "audio_bed": {"start": 1, "end": 2}})


def test_dissolves_are_entered_after_the_picture_settles():
    an = trailer()
    an.d["cuts_in"] = [round(c + 0.1, 2) for c in an.d["cuts"]]      # every transition is a 0.1 s dissolve
    ins, outs = set(an.d["cuts_in"]) | {0.0}, set(an.d["cuts"]) | {120.0}
    for c in build(an, RULES, 0, 120, 2):
        for m in c["moments"]:
            assert m["start"] in ins and m["end"] in outs                # never shows a half-blended frame



def character_spot():
    """60 s trailer: fast cuts every 1.2 s and six spoken lines, some crossing camera cuts."""
    n = 600
    rng = np.random.default_rng(1)
    texts = ["My name is Leon and I am the last of my house!", "My mouth gets ahead of my thoughts sometimes.",
             "I owe you my life.", "I would live and die a noble.", "This is the real world, little lordling.",
             "Then it can die alongside you!"]
    starts = [2.0, 12.5, 20.3, 28.1, 39.0, 47.6]
    segs, words = [], []
    for t, s0 in zip(texts, starts):
        ws = t.split()
        for k, w in enumerate(ws):
            words.append({"w": w, "s": round(s0 + 0.32 * k, 2), "e": round(s0 + 0.32 * k + 0.28, 2)})
        segs.append({"s": s0, "e": round(s0 + 0.32 * len(ws), 2), "t": t})
    data = {"cuts": [round(1.2 * k, 2) for k in range(1, 50)], "motion": list(0.02 + rng.random(n) * 0.05),
            "luma": [0.3] * n, "detail": [1.0] * n, "energy": list(-25 + rng.normal(0, 3, n)),
            "faces": [{"t": i / 5, "d": []} for i in range(300)], "beats": [], "onsets": [], "fps": 24}
    return Analysis(data, {"words": words, "segments": segs})


def test_dialogue_edit_keeps_every_line_whole_and_differs_between_clips():
    an = character_spot()
    rules = {"min_seconds": 40, "max_seconds": 50, "num_clips": 2, "target_seconds": 0}
    clips = build(an, rules, 0, 60, 2)
    assert len(clips) == 2                                              # short source: shots may be reused
    lines = an.tr["segments"]
    for c in clips:
        total = sum(s["end"] - s["start"] for s in c["segments"])
        assert 39 <= total <= 51 and len(c["segments"]) >= 5            # an edit, not one stretch
        for m in c["moments"]:
            for t in (m["start"], m["end"]):
                assert not any(g["s"] + 0.05 < t < g["e"] - 0.05 for g in lines)    # never cuts a line
        assert c["opening_line"] and c["closing_line"] and c["opening_line"] != c["closing_line"]
        assert c["segments"] != sorted(c["segments"], key=lambda s: s["start"])  # re-ordered, not the source order
        assert cut_report(c, an)["chopped"] == 0
    assert clips[0]["opening_line"] != clips[1]["opening_line"]
    assert clips[0]["closing_line"] != clips[1]["closing_line"]
