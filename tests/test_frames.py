import numpy as np

from clipper import spec as S
from clipper.frames import STYLES, Frame, _tokens
from clipper.pipeline import enforce_brief


def spec_with(style, **frame):
    sp = S.new_spec(title={"text": "Last of my bloodline", "show": False, "seconds": 4}, frame={"style": style, **frame})
    return S.normalize(sp)


def test_every_style_outputs_a_full_canvas_with_the_picture_inside():
    for style in STYLES:
        f = Frame(spec_with(style, progress=True), 1080, 1920, 20.0)
        x, y, w, h = f.box
        assert 0 <= x and 0 <= y and x + w <= 1080 and y + h <= 1920
        content = np.full((h, w, 3), 77, np.uint8)
        img = f.place(content, 10.0)
        assert img.shape == (1920, 1080, 3)
        assert (img[y + h // 2, x + w // 2] == 77).all()                  # picture shows inside its box


def test_headline_bar_and_cinema_band_layout():
    head = Frame(spec_with("headline"), 1080, 1920, 20.0)
    assert head.box[1] > 300 and head.box[3] == 1920 - head.box[1]        # bar on top, video fills the rest
    cine = Frame(spec_with("cinema"), 1080, 1920, 20.0)
    assert cine.box[2:] == (1080, 606) or cine.box[2:] == (1080, 608)     # 16:9 shown uncropped
    assert cine.caption_margin() < 1920 - (cine.box[1] + cine.box[3])     # captions sit below the picture


def test_progress_bar_fills_with_time():
    f = Frame(spec_with("none", progress=True), 1080, 1920, 20.0)
    black = np.zeros((1920, 1080, 3), np.uint8)
    half = f.place(black.copy(), 10.0)
    assert half[-2, 300].any() and not half[-2, 800].any()


def test_brief_that_bans_on_screen_text_removes_the_headline():
    sp = spec_with("headline")
    warnings = enforce_brief(sp, {"allows_on_screen_text": "no"})
    assert sp["frame"]["no_text"] and any("headline" in w for w in warnings)
    assert Frame(sp, 1080, 1920, 20.0).box[1] == 0                       # no bar without a headline


def test_headline_highlights_marked_word_else_longest_meaningful_word():
    assert [w for w, m in _tokens("Last of my *bloodline* speech", True) if m] == ["BLOODLINE"]
    assert [w for w, m in _tokens("this speech goes really hard", False) if m] == ["really"]
    assert not any(m for _, m in _tokens("go now", False))                  # too short to emphasise


def test_framed_canvas_fades_to_black_once():
    sp = spec_with("square")
    sp["ending"] = {"fade": 1.0, "to_black": 1.0}
    f = Frame(sp, 1080, 1920, 10.0)
    x, y, w, h = f.box
    end = f.place(np.full((h, w, 3), 200, np.uint8), 9.99)
    assert end.max() < 10                                                   # whole canvas, picture included


def test_titles_become_sentence_case_for_post_style_text():
    from clipper.frames import sentence_case
    assert sentence_case("Last Of My Bloodline Speech Goes Hard") == "Last of my bloodline speech goes hard"
    assert sentence_case("DMZ Early Access Is Here") == "DMZ early access is here"
    assert sentence_case("already normal text here") == "already normal text here"
