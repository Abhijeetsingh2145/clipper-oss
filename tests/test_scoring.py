from clipper.analysis import Analysis
from clipper.scoring import score_clip

RULES = {"min_seconds": 10, "max_seconds": 60, "banned_words": [], "content_focus": ""}


def make(words_text, start_at=0.0, dur=20.0, energy=None):
    """Synthetic analysis: words spread evenly from start_at, flat motion, no cuts."""
    toks = words_text.split()
    span = dur - start_at - 0.5
    step = span / len(toks)
    words = [{"w": w, "s": start_at + i * step, "e": start_at + i * step + step * 0.8} for i, w in enumerate(toks)]
    data = {"cuts": [], "motion": [0.01] * int(dur * 10), "faces": [{"t": i / 5, "d": []} for i in range(int(dur * 5))],
            "energy": energy or [-20.0] * int(dur * 10)}
    return Analysis(data, {"words": words, "segments": []})


SENTENCE = ("Most people quit right before it starts working and I almost did too. "
            "Then one small video changed everything for my business that year. ") * 2


def parts(an):
    return score_clip(0, 20, an, RULES, use_llm=False)["parts"]


def test_dangling_opener_lowers_self_contained():
    clean = parts(make(SENTENCE))["self_contained"]["score"]
    dangling = parts(make("So " + SENTENCE))["self_contained"]["score"]
    assert dangling < clean


def test_slow_start_lowers_hook():
    fast = parts(make(SENTENCE, start_at=0.2))["hook"]["score"]
    slow = parts(make(SENTENCE, start_at=3.0))["hook"]["score"]
    assert slow < fast


def test_mid_sentence_ending_lowers_self_contained():
    ends = parts(make(SENTENCE.strip()))["self_contained"]["score"]
    cut = parts(make(SENTENCE.strip().rstrip(".") + " and then"))["self_contained"]["score"]
    assert cut < ends


def test_setup_ending_lowers_payoff():
    done = parts(make(SENTENCE.strip()))["payoff"]["score"]
    setup = parts(make(SENTENCE.strip() + " If you remember one thing, remember this."))["payoff"]["score"]
    assert setup < done


def test_number_and_question_hooks_detected_without_model():
    plain = parts(make(SENTENCE))["hook"]
    hooky = parts(make("Did you know 90% of startups fail? " + SENTENCE))["hook"]
    assert hooky["score"] > plain["score"]
    assert "question" in hooky["evidence"] and "specific number" in hooky["evidence"]


def test_banned_word_zeroes_fit():
    an = make("this is a damn good clip " * 10)
    sc = score_clip(0, 20, an, {**RULES, "banned_words": ["damn"]}, use_llm=False)
    assert sc["parts"]["fit"]["score"] == 0


def test_every_part_has_evidence_and_total_is_bounded():
    sc = score_clip(0, 20, make(SENTENCE), RULES, use_llm=False)
    assert 0 <= sc["total"] <= 100
    assert all(p["evidence"] for p in sc["parts"].values())
    assert "not a view prediction" in sc["note"]


def test_quotes_in_hindi_are_recognised():
    from clipper.scoring import _norm
    assert _norm("किसी का भी किसी को भी!") == "किसी का भी किसी को भी"
    assert _norm("Kya   scene hai?") == "kya scene hai"
