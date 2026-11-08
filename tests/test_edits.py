from clipper import spec as S
from clipper.enrich import filler_cuts, pause_cuts, retake_cuts


def W(text, start=0.0, step=0.5, dur=0.4):
    return [{"w": w, "s": start + i * step, "e": start + i * step + dur} for i, w in enumerate(text.split())]


def test_filler_words_are_cut():
    words = W("so um I think uh this works")
    cuts = filler_cuts(words)
    assert [round(c[0], 1) for c in cuts] == [0.5, 2.0]


def test_long_pauses_shortened_short_kept():
    words = [{"w": "a", "s": 0.0, "e": 0.4}, {"w": "b", "s": 0.6, "e": 1.0}, {"w": "c", "s": 3.0, "e": 3.4}]
    cuts = pause_cuts(words, 0.0, 3.4, keep=0.3)
    assert len(cuts) == 1                       # only the 2 s gap
    gap_left = (3.0 - 1.0) - (cuts[0][1] - cuts[0][0])
    assert abs(gap_left - 0.3) < 1e-6


def test_retake_keeps_last_take():
    words = W("the best way to start is the best way to start is shipping daily")
    cuts = retake_cuts(words)
    assert cuts and cuts[0][0] == 0.0 and cuts[0][1] < words[6]["s"]


def test_repeated_filler_phrase_is_not_a_retake():
    assert retake_cuts(W("you know you know it is what it is")) == []


def test_timeline_and_caption_sync_after_cuts():
    spec = S.new_spec(segments=[{"start": 10.0, "end": 20.0}], cuts=[[12.0, 14.0]],
                      words=[{"w": "before", "s": 11.0, "e": 11.5}, {"w": "gone", "s": 12.5, "e": 13.0},
                             {"w": "after", "s": 15.0, "e": 15.5}])
    assert S.timeline(spec) == [(10.0, 12.0, 0.0), (14.0, 20.0, 2.0)]
    assert abs(S.duration(spec) - 8.0) < 1e-9
    out = {w["w"]: w["s"] for w in S.output_words(spec)}
    assert "gone" not in out and out["before"] == 1.0 and out["after"] == 3.0


def test_deleted_word_is_cut_from_video():
    spec = S.new_spec(segments=[{"start": 0.0, "end": 5.0}],
                      words=[{"w": "keep", "s": 1.0, "e": 1.5}, {"w": "drop", "s": 2.0, "e": 2.5, "deleted": True}])
    assert S.duration(spec) == 4.5


def test_reordered_segments_play_in_order():
    spec = S.new_spec(segments=[{"start": 30.0, "end": 33.0}, {"start": 5.0, "end": 7.0}])
    assert [round(o, 1) for _, _, o in S.timeline(spec)] == [0.0, 3.0]


def test_dissolve_overlaps_sections_and_keeps_captions_on_their_words():
    from clipper import spec as S
    sp = S.new_spec(segments=[{"start": 10, "end": 12}, {"start": 50, "end": 53}],
                    words=[{"w": "b", "s": 50.5, "e": 50.8}], join={"type": "dissolve", "dur": 0.2})
    pieces, joins = S.timeline_joins(sp)
    assert joins == {1: 0.2} and abs(pieces[1][2] - 1.8) < 1e-9          # second section starts 0.2 s early
    assert abs(S.duration(sp) - 4.8) < 1e-9                               # 2 + 3 - overlap
    assert abs(S.output_words(sp)[0]["s"] - 2.3) < 1e-9                   # caption moved with its picture
    sp["join"] = {"type": "flash"}
    assert S.timeline_joins(sp)[1] == {1: 0.0} and S.duration(sp) == 5    # instant joins don't overlap
