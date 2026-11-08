"""Clip quality score: a weighted sum of measured parts, each with the evidence behind it.

This is an estimate of how well a clip is built for short-form (hook, completeness, payoff,
pacing, visual energy, audio dynamics, campaign fit). It is NOT a view prediction. Once real
results are logged (analytics.py), the weights are refit to what actually performed for you and
the score says how many of your clips it was calibrated on.
"""
import json
import re
from pathlib import Path

import numpy as np

from .llm import ask_json

WEIGHTS_FILE = Path(__file__).resolve().parent.parent / "cache" / "score_weights.json"
DEFAULT_WEIGHTS = {"content": 30, "hook": 20, "self_contained": 12, "payoff": 12, "pacing": 10, "visual": 8, "audio": 3, "fit": 5}
LABELS = {"content": "Worth watching", "hook": "Hook (first 3 s)", "self_contained": "Makes sense on its own", "payoff": "Payoff",
          "pacing": "Pacing", "visual": "Visual energy", "audio": "Audio dynamics", "fit": "Campaign fit"}
# Openers that point back to something the viewer never saw
DANGLING = {"he", "she", "they", "them", "it", "that", "this", "those", "these", "so", "and", "but", "because", "which",
            "also", "then", "or", "plus", "anyway", "yeah", "right"}

RUBRIC = {
    "type": "object",
    "properties": {
        "hook_bold_claim": {"type": "boolean"}, "hook_conflict": {"type": "boolean"}, "hook_curiosity_gap": {"type": "boolean"},
        "stands_alone": {"type": "boolean"}, "stands_alone_reason": {"type": "string"},
        "payoff_type": {"type": "string", "enum": ["none", "clear_point", "punchline", "reveal", "emotional_peak"]},
        "payoff_strength": {"type": "integer", "minimum": 0, "maximum": 3},
        "payoff_quote": {"type": "string"},
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 10}, "fit_reason": {"type": "string"},
        "funny": {"type": "integer", "minimum": 0, "maximum": 3},
        "importance": {"type": "integer", "minimum": 0, "maximum": 3}, "importance_reason": {"type": "string"},
        "is_filler": {"type": "boolean"}, "key_quote": {"type": "string"},
    },
    "required": ["hook_bold_claim", "hook_conflict", "hook_curiosity_gap", "stands_alone", "stands_alone_reason",
                 "payoff_type", "payoff_strength", "payoff_quote", "fit_score", "fit_reason",
                 "funny", "importance", "importance_reason", "is_filler", "key_quote"],
}
MODEL_HOOK_KEYS = {"hook_bold_claim": "bold claim", "hook_conflict": "conflict/stakes", "hook_curiosity_gap": "curiosity gap"}
NUMBER_RE = re.compile(r"\b(\d[\d,.]*%?|zero|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b", re.I)
# endings that promise something the clip doesn't deliver
SETUP_END = re.compile(r"(\?|:|\b(this|here's|here is|the thing is|listen|remember this|what happened next)[.!]?)$", re.I)


def _norm(t):
    """Lowercase letters/digits of ANY script (Hindi quotes must match Hindi text), single spaces."""
    import unicodedata
    kept = "".join(ch for ch in t.lower() if not unicodedata.category(ch)[0] in "PS")   # keeps vowel signs (Mn/Mc)
    return re.sub(r"\s+", " ", kept).strip()


def weights():
    if WEIGHTS_FILE.exists():
        d = json.loads(WEIGHTS_FILE.read_text())
        return {**DEFAULT_WEIGHTS, **d["weights"]}, d.get("n", 0)
    return DEFAULT_WEIGHTS, 0


def _lin(x, x0, x1):
    """0 at x0, 100 at x1 (either direction), clamped."""
    if x1 == x0:
        return 100.0
    return float(np.clip((x - x0) / (x1 - x0), 0, 1) * 100)


def _band(x, lo_zero, lo_full, hi_full, hi_zero):
    """100 inside [lo_full, hi_full], falling linearly to 0 at lo_zero / hi_zero."""
    if x < lo_full:
        return _lin(x, lo_zero, lo_full)
    if x > hi_full:
        return _lin(x, hi_zero, hi_full)
    return 100.0


def video_stats(an):
    m = np.array(an.d["motion"] or [0.0])
    en = np.array([e for e in an.d.get("energy", []) if e > -60] or [-30.0])
    return {"motion_sorted": np.sort(m), "energy_median": float(np.median(en))}


def _pct_rank(sorted_arr, v):
    return float(np.searchsorted(sorted_arr, v) / max(len(sorted_arr), 1) * 100)


def _cut_rate_score(cuts, dur):
    per10 = len(cuts) / max(dur, 0.1) * 10
    return _band(per10, 0.0, 1.5, 6.0, 14.0), per10


def laugh_stats(laughs, s, e):
    """Audience laughter belonging to a clip: laughs that start inside it (or within 1 s of its end)."""
    own = [(a, b) for a, b in laughs if s <= a <= e + 1.0]
    secs = sum(b - a for a, b in own)
    final = max((b - a for a, b in own if b >= e - 1.0), default=0.0)     # the laugh the clip ends on
    return len(own), secs, final


def _laugh_rank(laughs, s, e, duration_total):
    """Laughter per minute of this clip vs every same-length window of the whole set (percentile)."""
    n, secs, final = laugh_stats(laughs, s, e)
    dur = max(e - s, 1.0)
    rate = secs / dur * 60
    if not laughs:
        return 0.0, n, secs, final
    step = max(dur / 4, 5.0)
    rates = [laugh_stats(laughs, t, t + dur)[1] / dur * 60 for t in np.arange(0, max(duration_total - dur, 1), step)]
    return _pct_rank(np.sort(rates), rate), n, secs, final


def score_clip(s, e, an, rules, model=None, prompt="", stats=None, use_llm=True, mode=None):
    stats = stats or video_stats(an)
    dur = max(e - s, 0.1)
    words = an.words_between(s, e)
    speech = len(words) / dur * 60 >= 25
    cuts = an.cuts_between(s + 0.2, e - 0.2)
    llm = None
    if use_llm and speech:
        text = " ".join(w["w"] for w in words)
        third = [w for w in words if w["s"] >= s + dur * 2 / 3]
        first_sentence = re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]
        focus = "; ".join(x for x in (rules.get("content_focus", ""), prompt) if x) or ""
        try:
            llm = _rubric(first_sentence, third, text, focus, model, mode)
        except Exception:                          # model unreachable: the measured parts still score
            llm = None
    parts = _score_parts(s, e, an, rules, words, speech, dur, cuts, llm, stats, prompt, mode)
    return parts


CONTENT_GUIDE = {
    "comedy": "funny 0-3: how hard would a stranger laugh at THIS bit on its own (0 not funny, 1 a smile, 2 a real laugh, "
              "3 a big laugh). Judge the jokes, not the topic. importance: 0. key_quote: copy the funniest line exactly.",
    None: "importance 0-3: how much this matters or helps a viewer (0 small talk / ads / housekeeping / rambling, "
          "1 mildly interesting, 2 a clear useful insight, story or strong opinion, 3 a surprising, important or "
          "memorable point). is_filler: true for intros, outros, sponsor reads, banter about the show itself. "
          "key_quote: copy the single most important line exactly. funny: 0-3 if it is funny, else 0.",
}


def _rubric(first_sentence, third, text, focus, model, mode=None):
    return ask_json(
            "Grade this short-video clip strictly, like a skeptical editor. Most clips are average; do not be generous.\n"
            + CONTENT_GUIDE.get(mode if mode == "comedy" else None) + "\n"
            "Hook questions are about the FIRST SENTENCE only: bold_claim = a surprising or strong statement; "
            "conflict = stakes, a problem or disagreement; curiosity_gap = it makes you need to know what comes next.\n"
            "stands_alone: would a stranger understand the clip with no prior context?\n"
            "Payoff is about the LAST THIRD only. Pick the ONE best payoff_type, or 'none' if the clip ends on a setup, "
            "a question or a half-made point. payoff_strength: 0 none, 1 weak, 2 solid, 3 memorable. "
            "payoff_quote: copy the exact payoff words from the LAST THIRD.\n"
            f"fit_score 0-10: how well the clip matches this focus: {focus or 'no focus given, answer 5'}\n\n"
            f"FIRST SENTENCE: {first_sentence}\n\nLAST THIRD: {' '.join(w['w'] for w in third)}\n\nFULL CLIP: {text[:2500]}",
            RUBRIC, model, temperature=0,
        )


def _score_parts(s, e, an, rules, words, speech, dur, cuts, llm, stats, prompt, mode=None):
    en = [x for x in an.energy_between(s, e) if x > -60]
    motion = an.motion_between(s, e)
    parts = {}

    # --- worth watching: is it actually funny / does the topic matter? ---
    text_all = _norm(" ".join(w["w"] for w in words))
    quote = (llm or {}).get("key_quote", "")
    quoted_ok = bool(_norm(quote)) and _norm(quote)[:30] in text_all          # the model must quote the clip
    if mode == "comedy":
        if "laughs" not in stats:
            from .modes import laughs
            stats = {**stats, "laughs": laughs(an), "duration": len(an.d.get("energy") or []) * 0.1}
        rank, n_l, secs, final = _laugh_rank(stats.get("laughs") or [], s, e, stats.get("duration") or e)
        funny = int(llm.get("funny", 0)) if llm and quoted_ok else None
        val = 0.65 * rank + 0.35 * (funny / 3 * 100 if funny is not None else rank)
        ev = (f"{n_l} audience laughs, {secs:.1f}s of laughter ({rank:.0f}th percentile of this set) · "
              f"final laugh {final:.1f}s" + (f' · AI funniness {funny}/3: "{quote[:70]}"' if funny is not None else ""))
        parts["content"] = (val, ev)
    elif speech and llm:
        imp = int(llm.get("importance", 0)) if quoted_ok else 0
        val = 0.0 if llm.get("is_filler") else imp / 3 * 100
        funny = int(llm.get("funny", 0))
        val = max(val, funny / 3 * 80)                               # a genuinely funny aside also earns its place
        ev = ("Filler (intro / ad / banter about the show)" if llm.get("is_filler") else
              f'Importance {imp}/3: {llm.get("importance_reason", "")[:90]} · key line "{quote[:70]}"'
              + ("" if quoted_ok else " (quote not found in the clip, so it doesn't count)"))
        parts["content"] = (val, ev)
    elif speech:
        parts["content"] = (50.0, "AI model unavailable: topic importance not judged (neutral)")
    else:
        parts["content"] = (_pct_rank(stats["motion_sorted"], float(np.mean(motion)) if motion else 0),
                            "Visual clip: judged by action (motion percentile)")

    # --- hook ---
    first_en = en[: 30] if en else []
    loud_diff = (np.mean(first_en) - np.median(en)) if len(en) > 35 else 0.0
    loud_score = _lin(loud_diff, -6, 3)
    if speech:
        first_word = words[0]["s"] - s
        start_score = 100.0 if first_word <= 0.5 else max(0.0, 100 * (1 - (first_word - 0.5) / 2.5))
        first_sentence = re.split(r"(?<=[.?!])\s", " ".join(w["w"] for w in words), maxsplit=1)[0]
        # devices checked directly in the text...
        hits = []
        if first_sentence.rstrip().endswith("?"):
            hits.append("question")
        if NUMBER_RE.search(first_sentence):
            hits.append("specific number")
        if re.search(r"\byou(r|'re)?\b", first_sentence, re.I):
            hits.append("speaks to 'you'")
        # ...and the ones that need judgment, from the model
        if llm:
            hits += [label for k, label in MODEL_HOOK_KEYS.items() if llm.get(k)]
        rubric = min(100, 25 * len(hits))
        hook = 0.4 * rubric + 0.3 * start_score + 0.3 * loud_score
        ev = (f'Speech starts at {first_word:.1f}s · first 3s {loud_diff:+.1f} dB vs rest · "{first_sentence[:90]}" → '
              f'{", ".join(hits) or "no hook devices"}' + ("" if llm else " (judgment checks skipped)"))
    else:
        m3 = np.mean(motion[:30]) if motion else 0
        vis = _pct_rank(stats["motion_sorted"], m3)
        early_cuts = len([c for c in cuts if c < s + 3])
        vis = min(100, vis + 10 * early_cuts)
        hook = 0.5 * vis + 0.5 * loud_score
        ev = f"First 3s motion at {vis:.0f}th percentile of this video, {early_cuts} cut(s) · {loud_diff:+.1f} dB vs rest"
    parts["hook"] = (hook, ev)

    # --- self-contained ---
    if speech:
        fw = re.sub(r"[^\w']", "", words[0]["w"]).lower()
        dangling = fw in DANGLING
        ends_clean = words[-1]["w"][-1:] in ".!"      # ending on a question leaves the viewer hanging
        alone = llm["stands_alone"] if llm else True
        sc = 100 - 35 * dangling - 30 * (not ends_clean) - 35 * (not alone)
        ev = (f'Opens with "{words[0]["w"]}"{" (refers back to earlier context)" if dangling else ""} · '
              f'{"ends on a full sentence" if ends_clean else ("ends on a question" if words[-1]["w"].endswith("?") else "ends mid-sentence")}'
              + (f" · {llm['stands_alone_reason'][:100]}" if llm else ""))
    else:
        near_start = any(abs(c - s) < 0.5 for c in an.d["cuts"])
        near_end = any(abs(c - e) < 0.5 for c in an.d["cuts"])
        sc = 50 * near_start + 50 * near_end
        ev = f"{'Starts' if near_start else 'Does not start'} on a camera cut · {'ends' if near_end else 'does not end'} on one"
    parts["self_contained"] = (max(0, sc), ev)

    # --- payoff ---
    if speech:
        tail = " ".join(w["w"] for w in words[-12:]).strip()
        setup_end = bool(SETUP_END.search(tail))
        structural = 0 if setup_end else (100 if words[-1]["w"][-1:] in ".!" else 40)
        if llm:
            last_text = _norm(" ".join(w["w"] for w in words if w["s"] >= s + dur * 0.55))
            quote = llm.get("payoff_quote", "")
            quoted_ok = bool(_norm(quote)) and _norm(quote)[:40] in last_text     # the model must quote the real ending
            strength = int(llm.get("payoff_strength", 0)) if quoted_ok and llm.get("payoff_type") != "none" else 0
            rubric = min(3, max(0, strength)) / 3 * 100
            payoff = 0.7 * rubric + 0.3 * structural
            ev = (f'"{quote[:90]}" → {llm.get("payoff_type", "none").replace("_", " ")}, strength {strength}/3'
                  + ("" if quoted_ok else " (quote not found in the ending, so it doesn't count)"))
        else:
            payoff, ev = structural, "Judged from the ending's structure only"
        if setup_end:
            ev += f' · ends on a setup ("…{tail[-40:]}")'
        parts["payoff"] = (payoff, ev)
    if not speech:
        last = en[int(len(en) * 2 / 3):] if en else []
        climax = (max(last) - np.median(en)) if last else 0
        parts["payoff"] = (_lin(climax, 0, 8), f"Loudest moment in the last third is {climax:+.1f} dB above the clip's median")

    # --- pacing ---
    if speech:
        wps = len(words) / dur
        spoken = sum(w["e"] - w["s"] for w in words)
        silence = max(0.0, 1 - spoken / dur)
        gaps = [b["s"] - a["e"] for a, b in zip(words, words[1:])] + [words[0]["s"] - s, e - words[-1]["e"]]
        longest = max(gaps) if gaps else 0
        pace = (0.6 * _band(wps, 1.0, 2.3, 3.4, 4.8) + 0.25 * _lin(silence, 0.5, 0.15)
                + 0.15 * (100 if longest < 1.2 else max(0, 100 - (longest - 1.2) * 50)))
        parts["pacing"] = (pace, f"{wps:.1f} words/s · {silence * 100:.0f}% without speech · longest pause {longest:.1f}s")
    else:
        cs, per10 = _cut_rate_score(cuts, dur)
        parts["pacing"] = (cs, f"{per10:.1f} cuts per 10 s")

    # --- visual energy ---
    mrank = _pct_rank(stats["motion_sorted"], float(np.mean(motion)) if motion else 0)
    cs, per10 = _cut_rate_score(cuts, dur)
    if speech:
        fshare = an.face_share(s, e)
        vis = 0.4 * mrank + 0.2 * cs + 0.4 * _lin(fshare, 0.0, 0.6)
        ev = f"Motion at {mrank:.0f}th percentile of this video · {per10:.1f} cuts/10s · face on screen {fshare * 100:.0f}% of the time"
    else:
        vis = 0.6 * mrank + 0.4 * cs
        ev = f"Motion at {mrank:.0f}th percentile of this video · {per10:.1f} cuts/10s"
    parts["visual"] = (vis, ev)

    # --- audio dynamics ---
    std = float(np.std(en)) if len(en) > 5 else 0
    parts["audio"] = (_band(std, 0.5, 3.0, 9.0, 16.0), f"Loudness varies by {std:.1f} dB (flat audio scores low)")

    # --- campaign fit + hard rules ---
    spoken_text = " ".join(w["w"] for w in words)
    banned = [b for b in rules.get("banned_words", []) if re.search(rf"\b{re.escape(b)}\b", spoken_text, re.IGNORECASE)]
    lo, hi = rules.get("min_seconds", 0), rules.get("max_seconds", 1e9)
    in_len = lo - 1 <= dur <= hi + 1
    if llm and (rules.get("content_focus") or prompt):
        fit, ev = llm["fit_score"] * 10, llm["fit_reason"][:120]
    else:
        fit, ev = 70.0, "No campaign focus or prompt to compare against (neutral)"
    if banned:
        fit, ev = 0.0, f"Banned words spoken: {', '.join(banned)}"
    if not in_len:
        fit, ev = fit * 0.5, ev + f" · length {dur:.0f}s outside {lo}-{hi}s"
    parts["fit"] = (float(np.clip(fit, 0, 100)), ev)

    w, n_cal = weights()
    total = sum(w[k] * parts[k][0] for k in parts) / sum(w[k] for k in parts)
    return {
        "total": int(round(total)),
        "parts": {k: {"label": LABELS[k], "score": int(round(v)), "weight": w[k], "evidence": ev} for k, (v, ev) in parts.items()},
        "kind": "speech" if speech else "visual",
        "calibrated_on": n_cal,
        "note": "Clip quality estimate, not a view prediction" + (f" · weights calibrated on {n_cal} of your posted clips" if n_cal else ""),
    }
