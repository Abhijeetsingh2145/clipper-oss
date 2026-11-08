"""Content types: what kind of video this is decides HOW it is edited.

A montage (best shots re-ordered) is right for a trailer and ruins stand-up, where a punchline
without its setup is not a joke. So every job resolves to one content type, either chosen in the UI
or detected here, and the type carries its own editing recipe:

- comedy:   one complete bit, setup to punchline, in order; ends after the audience laugh; natural
            comic pauses kept (0.6 s), fillers kept (they are delivery).
- podcast:  one self-contained story / opinion / answer, in order; fillers and dead air trimmed.
- talking:  vlog / talking head / tutorial: one self-contained point, tight trims.
- trailer:  re-cut from the best shots and spoken lines (montage.py), smooth crossfades.
- gameplay: action highlights from across the video, cut on sound hits.
- music:    the hook / chorus as ONE continuous section, starting and ending on a musical bar.

Detection is measured first (speech rate, audience laughter, beat regularity, camera-cut rate,
the file name) and asks the language model only when those signals don't settle it.
"""
import re

import numpy as np

MODES = {
    "comedy": {"label": "Stand-up / comedy", "edit": "bit", "pause_keep": 0.6, "fillers": False, "pauses": True, "join": "cut",
               "hint": "Complete bits from setup to punchline, ending after the laugh"},
    "podcast": {"label": "Podcast / interview", "edit": "moment", "pause_keep": 0.35, "fillers": True, "pauses": True, "join": "cut",
                "hint": "One self-contained story or opinion, trimmed tight"},
    "talking": {"label": "Talking head / vlog", "edit": "moment", "pause_keep": 0.25, "fillers": True, "pauses": True, "join": "cut",
                "hint": "One clear point, jump-cut tight"},
    "trailer": {"label": "Game trailer / ad", "edit": "montage", "pause_keep": 0.35, "fillers": False, "pauses": False, "join": "dissolve",
                "hint": "Re-cut from the best shots and lines"},
    "gameplay": {"label": "Gameplay", "edit": "montage", "pause_keep": 0.35, "fillers": False, "pauses": False, "join": "cut",
                 "hint": "Action highlights cut on sound hits"},
    "music": {"label": "Music / song", "edit": "song", "pause_keep": 0.35, "fillers": False, "pauses": False, "join": "cut",
              "hint": "The hook or chorus, on the beat, never re-ordered"},
}

NAME_HINTS = [
    ("comedy", r"stand.?up|comedy|comedian|special|roast|open mic|jokes?\b|\bbit\b|netflix is a joke"),
    ("podcast", r"podcast|\bep(isode)?\s*\d|interview|\bpod\b|conversation|talk ?show|panel"),
    ("music", r"official (music )?video|lyric|\baudio\b|\bsong\b|\bft\.|feat\.|remix|\bmv\b|music video|live at"),
    ("trailer", r"trailer|teaser|launch|reveal|sizzle|announcement|\bad\b|commercial|promo|cinematic"),
    ("gameplay", r"gameplay|walkthrough|playthrough|let'?s play|speedrun|clutch|\bkills?\b|ranked|stream highlights"),
]


def laughs(an, min_len=0.6):
    """Audience laughter / applause: stretches with no words that are nearly as loud as speech.
    Returns [(start, end)] in source seconds."""
    words = an.tr["words"]
    en = np.array(an.d.get("energy") or [])
    if len(words) < 20 or not len(en):
        return []
    speech_db = np.median([v for w in words for v in en[int(w["s"] / 0.1):int(w["e"] / 0.1) + 1] if v > -60] or [-30])
    out = []
    for a, b in zip(words, words[1:]):
        s, e = a["e"] + 0.15, b["s"] - 0.1
        if e - s < min_len:
            continue
        seg = en[int(s / 0.1):int(e / 0.1)]
        loud = seg > speech_db - 9
        if len(seg) and loud.mean() > 0.6:
            idx = np.flatnonzero(loud)
            out.append((round(s + idx[0] * 0.1, 2), round(s + (idx[-1] + 1) * 0.1, 2)))
    return out


def signals(tr, an, duration):
    mins = max(duration / 60, 1e-6)
    words = tr["words"]
    return {
        "words_per_min": round(len(words) / mins, 1),
        "laughs_per_min": round(len(laughs(an)) / mins, 2) if an else 0.0,
        "cuts_per_min": round(len(an.d["cuts"]) / mins, 1) if an else 0.0,
        "beat_strength": round(float(an.d.get("beat_strength") or 0), 2) if an else 0.0,
        "repeated_lines": _repeats(tr),
    }


def _repeats(tr):
    """How many lyric-like lines occur 2+ times (choruses repeat; speech almost never does)."""
    norm = [re.sub(r"[^\w ]", "", s["t"].lower()).strip() for s in tr["segments"]]
    norm = [n for n in norm if len(n.split()) >= 3]
    return sum(1 for n in set(norm) if norm.count(n) >= 2)


def detect(tr, an, duration, filename="", model=None):
    """(mode, reason). Measured signals first, the language model only for the undecided middle."""
    sig = signals(tr, an, duration)
    name = filename.lower()
    for mode, pat in NAME_HINTS:
        if re.search(pat, name):
            return mode, f"file name suggests {MODES[mode]['label'].lower()} · " + _fmt(sig)
    wpm, lpm, cpm = sig["words_per_min"], sig["laughs_per_min"], sig["cuts_per_min"]
    if wpm < 25:                                                       # little speech: visual or musical
        if sig["beat_strength"] >= 0.3 and cpm < 15:
            return "music", "steady beat, little speech · " + _fmt(sig)
        return ("trailer" if cpm >= 15 else "gameplay"), "little speech · " + _fmt(sig)
    if sig["repeated_lines"] >= 2 and sig["beat_strength"] >= 0.25:
        return "music", "repeated lines over a steady beat (sung chorus) · " + _fmt(sig)
    if lpm >= 0.8:
        return "comedy", "frequent audience laughter between lines · " + _fmt(sig)
    if cpm >= 15:
        return "trailer", "fast cutting with dialogue · " + _fmt(sig)
    guess = _ask_model(tr, sig, model)
    if guess:
        return guess, "judged from the transcript by the AI model · " + _fmt(sig)
    return "podcast", "steady talking · " + _fmt(sig)


def _fmt(sig):
    return (f"{sig['words_per_min']:.0f} words/min, {sig['laughs_per_min']:.1f} laughs/min, "
            f"{sig['cuts_per_min']:.0f} cuts/min, beat {sig['beat_strength']:.2f}")


def _ask_model(tr, sig, model):
    try:
        from .llm import ask_json
        sample = " ".join(s["t"] for s in tr["segments"])[:2500]
        out = ask_json(
            "What kind of video is this? Pick one: comedy (stand-up or sketch), podcast (conversation / interview), "
            "talking (one person: vlog, tutorial, commentary), trailer (edited promo / ad), gameplay, music.\n"
            f"Measurements: {_fmt(sig)}.\nTranscript sample:\n{sample}",
            {"type": "object", "properties": {"type": {"type": "string", "enum": list(MODES)}}, "required": ["type"]},
            model, temperature=0)
        return out["type"] if out.get("type") in MODES else None
    except Exception:
        return None


def resolve(settings, tr, an, duration, filename, model):
    """Mode for this job: the user's choice, an old 'edit_style', or detection."""
    chosen = settings.get("mode")
    if chosen in MODES:
        return chosen, "chosen by you"
    legacy = settings.get("edit_style")
    if legacy == "montage":
        return "trailer", "edit style 'montage'"
    if legacy == "moment":
        return "podcast", "edit style 'single moment'"
    return detect(tr, an, duration, filename, model)


def apply_recipe(settings, mode):
    """The mode's trims and transition become this job's defaults."""
    r = MODES[mode]
    s = dict(settings)
    a = dict(s.get("audio") or {})
    a["pause_max"] = r["pause_keep"]
    a["remove_pauses"] = bool(a.get("remove_pauses", True)) and r["pauses"]
    a["remove_fillers"] = bool(a.get("remove_fillers", True)) and r["fillers"]
    s["audio"] = a
    s.setdefault("join", {"type": r["join"], "dur": 0.2})
    s["mode"] = mode
    return s
