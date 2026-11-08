from clipper import spec as S
from clipper.editor import apply_ops, delete_phrase, guard


def base():
    return S.new_spec(segments=[{"start": 10.0, "end": 30.0}],
                      words=[{"w": w, "s": 10 + i, "e": 10.5 + i} for i, w in enumerate("ship a post, a video every day".split())])


def test_guard_drops_unrequested_caption_args():
    ops = [{"op": "captions", "args": {"color": "yellow", "position": "top", "template": "bold_pop"}}]
    kept = guard(ops, "make the captions yellow")
    assert kept[0]["args"] == {"color": "yellow"}


def test_values_are_validated_and_clamped():
    sp, done = apply_ops(base(), [{"op": "captions", "args": {"template": "not-a-template", "words_per_line": 99}},
                                  {"op": "aspect", "args": {"aspect": "7:3"}},
                                  {"op": "zoom", "args": {"mode": "at", "t": 1, "scale": 9}}])
    assert sp["captions"]["template"] == "bold_pop" and sp["captions"]["words_per_line"] == 8
    assert sp["aspect"] == "9:16"
    assert sp["zooms"][0]["scale"] == 1.4


def test_delete_phrase_marks_words_and_shortens_clip():
    sp = base()
    assert delete_phrase(sp, "a post, a video") == 4
    assert S.duration(sp) < 20


def test_trim_moves_edges():
    sp, _ = apply_ops(base(), [{"op": "trim", "args": {"start_delta": -2, "end_delta": 3}}])
    assert sp["segments"][0]["start"] == 8.0 and sp["segments"][-1]["end"] == 33.0


def test_original_spec_untouched():
    b = base()
    apply_ops(b, [{"op": "aspect", "args": {"aspect": "1:1"}}])
    assert b["aspect"] == "9:16"
