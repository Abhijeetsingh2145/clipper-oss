"""Choose clip candidates, then rank them by the measured quality score (scoring.py).

Speech-led videos: the language model proposes moments from the transcript (steered by the
campaign focus and your own prompt), edges snap to sentences.
Visual videos (trailers, gameplay, music): candidates come from loudness + motion + cut density,
edges snap to camera cuts.
"""
import re

import numpy as np

from .common import log
from .llm import ask_json
from .scoring import SETUP_END, score_clip, video_stats

OVERLAP_S = 60

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                               "title": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["start", "end", "title", "reason"],
            },
        }
    },
    "required": ["clips"],
}

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"matches": {"type": "array", "items": {"type": "object", "properties": {
        "start": {"type": "number"}, "end": {"type": "number"}, "why": {"type": "string"}}, "required": ["start", "end", "why"]}}},
    "required": ["matches"],
}


def _range(settings, duration):
    t0 = max(0.0, float(settings.get("start") or 0))
    t1 = min(duration, float(settings.get("end") or duration))
    return (t0, t1) if t1 > t0 else (0.0, duration)


def pick_clips(tr, rules, duration, video, has_audio, model, an=None, settings=None):
    settings = settings or {}
    n, lo, hi = rules["num_clips"], rules["min_seconds"], rules["max_seconds"]
    t0, t1 = _range(settings, duration)
    prompt = (settings.get("prompt") or "").strip()
    if t1 - t0 <= lo:
        return [{"start": t0, "end": t1, "title": "", "score": None, "reason": "Selected range is shorter than the minimum length",
                 "source": "whole"}]
    words = [w for w in tr["words"] if t0 <= w["s"] < t1]
    speech = len(words) / max((t1 - t0) / 60, 1e-6) >= 25
    stats = video_stats(an)
    cuts_per_min = len(an.cuts_between(t0, t1)) / max((t1 - t0) / 60, 1e-6) if an else 0
    from .modes import MODES
    mode = settings.get("mode") or "podcast"
    recipe = MODES.get(mode, MODES["podcast"])
    if recipe["edit"] == "song":
        from .music import song_sections
        log("Music: finding the hook / chorus, cut on bar lines")
        clips = song_sections(tr, an, rules, t0, t1, n)
        if clips:
            for c in clips:
                sc = score_clip(c["start"], c["end"], an, rules, model, prompt, stats, use_llm=False)
                c["score"], c["score_parts"] = sc["total"], sc
            return clips
    if recipe["edit"] == "montage":
        from .montage import build, score_montage
        log(f"Building edits from the best {'lines and ' if speech else ''}shots across the video "
            f"({cuts_per_min:.0f} camera cuts/min{', dialogue kept whole' if speech else ''})")
        clips = build(an, rules, t0, t1, n, model=model, focus=" ".join(filter(None, [rules.get("content_focus", ""), prompt])))
        if clips:
            for c in clips:
                sc = score_montage(c, an, rules, stats)
                c["score"], c["score_parts"] = sc["total"], sc
            return sorted(clips, key=lambda c: -c["score"])
        log("Not enough distinct shots for a montage; using single moments instead")
    cands = []
    if speech:
        log(f"Finding {'complete bits' if mode == 'comedy' else 'candidate moments'} in the transcript"
            f"{' for: ' + prompt if prompt else ''}")
        laughs_ = None
        if mode == "comedy":
            from .modes import laughs
            laughs_ = laughs(an)
            log(f"Heard {len(laughs_)} audience laughs; bits end after the laugh")
        try:
            raw = _llm_candidates(tr, rules, t0, t1, model, prompt, want=max(n * 3, n + 4), mode=mode, laughs_=laughs_)
        except Exception as e:                      # model unreachable: measured fallbacks below
            log(f"The AI model could not be reached ({str(e)[:80]})")
            raw = []
        cands = [c for c in (_snap(c, tr, lo, hi, t0, t1) for c in raw) if c]
        if not cands and laughs_:
            log("Building bits backwards from the biggest audience laughs")
            cands = [c for c in (_snap(c, tr, lo, hi, t0, t1) for c in _laugh_candidates(laughs_, rules, t0, t1, n * 3)) if c]
        if laughs_:
            cands = [_through_laugh(c, laughs_, hi, t1) for c in cands]
        if not cands:
            log("The model found no usable moments; using visual/audio intensity instead")
    if not cands:
        if not speech:
            log("Little or no speech: finding the most intense sections (loudness, motion, cuts)")
        cands = _moment_candidates(an, rules, t0, t1, n * 3)

    cands = _dedupe_overlaps(cands)
    log(f"Scoring {len(cands)} candidates")
    if mode == "comedy":
        from .modes import laughs
        stats = {**stats, "laughs": laughs(an), "duration": t1}
    for c in cands:
        sc = score_clip(c["start"], c["end"], an, rules, model, prompt, stats, mode=mode)
        c["score"], c["score_parts"] = sc["total"], sc
    return _pick_best(_worth_watching(cands, n, mode, stats), n)


def _worth_watching(cands, n, mode, stats):
    """Drop clips that aren't worth a viewer's time: comedy bits with fewer than 2 audience laughs,
    talk clips judged filler / unimportant. Weak ones only stay if nothing better exists."""
    def good(c):
        content = c["score_parts"]["parts"].get("content", {}).get("score", 50)
        if mode == "comedy":
            from .scoring import laugh_stats
            return laugh_stats(stats.get("laughs") or [], c["start"], c["end"])[0] >= 2
        return content >= 34
    keep = [c for c in cands if good(c)]
    if len(keep) < n:
        log(f"Only {len(keep)} candidate(s) clearly worth watching; filling with the next best")
        content = lambda c: c["score_parts"]["parts"].get("content", {}).get("score", 0)
        keep += sorted((c for c in cands if c not in keep), key=lambda c: (-content(c), -(c["score"] or 0)))[:n - len(keep)]
    else:
        log(f"Kept {len(keep)} of {len(cands)} candidates as worth watching")
    return keep


MODE_BRIEFS = {
    "comedy": ("This is STAND-UP COMEDY. Each clip must be ONE COMPLETE BIT in its original order: start where a "
               "new premise or setup begins (never mid-story), include every line the punchline depends on, and end "
               "on the punchline that gets the biggest laugh ([LAUGH] markers show where the audience laughed and "
               "for how long). Prefer bits with several laughs that build to the biggest one. Never end on a setup."),
    "podcast": ("This is a PODCAST / INTERVIEW. Each clip must be one self-contained story, opinion or answer that a "
                "stranger understands without the rest of the episode: include the question or context it needs, "
                "and end when the point lands."),
    "talking": ("This is a TALKING-HEAD video (vlog, tutorial, commentary). Each clip must make one clear point "
                "with its context, ending when the point lands."),
}


def _llm_candidates(tr, rules, t0, t1, model, prompt, want, mode="podcast", laughs_=None):
    segs, out = tr["segments"], []
    window = max(480, rules["max_seconds"] * 2 + 60)       # long clips (up to 15 min) need a bigger view
    focus = rules.get("content_focus") or "moments most likely to work as standalone short clips"
    start = t0
    while start < t1:
        end = min(t1, start + window)
        chunk = [s for s in segs if s["e"] > start and s["s"] < end]
        if chunk:
            def line(s):
                after = [b - a for a, b in (laughs_ or []) if s["e"] - 0.3 <= a <= s["e"] + 1.2]
                return f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}" + (f"   [LAUGH {max(after):.1f}s]" if after else "")
            lines = "\n".join(line(s) for s in chunk)
            reply = ask_json(
                (f"USER INSTRUCTION (most important): {prompt}\n\n" if prompt else "")
                + MODE_BRIEFS.get(mode, MODE_BRIEFS["podcast"]) + "\n"
                + "Below is part of the transcript, in its own language. Each line is [start-end seconds] text.\n"
                f"Pick up to {want} moments for short clips. Each must be {rules['min_seconds']}-{rules['max_seconds']} seconds"
                + (f" (aim for about {rules['target_seconds']} seconds)" if rules.get("target_seconds") else "")
                + ", open with a strong hook in the first sentence, make sense to a stranger with no context, "
                "end on a payoff, and never cut mid-sentence.\n"
                f"Campaign focus: {focus}\n"
                f"Avoid anything containing: {', '.join(rules['banned_words']) or 'nothing specified'}\n"
                "Use start/end times from the line timestamps. Title: a short punchy hook. Reason: why it works, in one line."
                f"\n\nTRANSCRIPT:\n{lines}",
                CLIP_SCHEMA, model,
            )
            for c in reply.get("clips", []):
                if start - 5 <= c["start"] < c["end"] <= end + 60:
                    c["source"] = "transcript" + (" + prompt" if prompt else "")
                    out.append(c)
        if end >= t1:
            break
        start = end - OVERLAP_S
    return out


def _snap(c, tr, lo, hi, t0, t1):
    """Move clip edges to sentence boundaries and enforce the length rules."""
    segs = [s for s in tr["segments"] if s["e"] > t0 and s["s"] < t1]
    if not segs:
        return None
    first = min(segs, key=lambda s: abs(s["s"] - c["start"]))
    last = min(segs, key=lambda s: abs(s["e"] - c["end"]))
    s, e = first["s"], last["e"]
    if e <= s:
        return None
    following = [g for g in segs if g["s"] >= e - 0.05]
    while e - s < lo and following:
        e = following.pop(0)["e"]
    # don't stop on a setup ("…remember this.", "here's why:", a question): include the answer if it fits
    ending = next((g["t"] for g in reversed(segs) if g["e"] <= e + 0.05), "")
    while following and SETUP_END.search(ending.strip()) and following[0]["e"] - s <= hi:
        nxt = following.pop(0)
        e, ending = nxt["e"], nxt["t"]
    if e - s > hi:
        words = [w for w in tr["words"] if s <= w["s"] and w["e"] <= s + hi]
        if not words:
            return None
        enders = [w for w in words if w["w"][-1:] in ".?!" and w["e"] - s >= lo]
        e = (enders[-1] if enders else words[-1])["e"]
    # end in the natural pause after the last sentence (up to 0.8 s), not right on the final syllable
    nxt = next((w["s"] for w in tr["words"] if w["s"] > e + 0.05), e + 0.8)
    tail = max(0.25, min(0.8, nxt - e - 0.1))
    s, e = max(t0, s - 0.15), min(t1, e + tail)
    if not lo - 1 <= e - s <= hi + 1:
        return None
    return {**c, "start": round(s, 2), "end": round(e, 2)}


def _laugh_candidates(laughs_, rules, t0, t1, k):
    """Without the AI model: the longest laughs mark the best punchlines; each bit ends on the
    line before the laugh and starts about the target length earlier (snapped to a sentence)."""
    target = rules.get("target_seconds") or min(max(rules["min_seconds"], 40), rules["max_seconds"])
    out = []
    for a, b in sorted(laughs_, key=lambda x: x[0] - x[1]):           # longest laugh first
        if a - target < t0 or any(abs(a - c["end"]) < target for c in out):
            continue
        out.append({"start": a - target, "end": a, "title": "", "source": "laughter",
                    "reason": f"Ends on a punchline that got a {b - a:.1f}s laugh (measured)"})
        if len(out) >= k:
            break
    return out


def _through_laugh(c, laughs_, hi, t1):
    """Stand-up: never cut the audience off. If a laugh starts right after the clip's last line,
    run on until it fades (capped so the clip stays within the max length)."""
    e = c["end"]
    for a, b in laughs_:
        if e - 1.0 <= a <= e + 1.0 and b > e:
            new_e = min(b + 0.3, t1, c["start"] + hi + 2)
            return {**c, "end": round(new_e, 2), "reason": (c.get("reason") or "") + f" · ends after a {b - a:.1f}s laugh"}
    return c


def _zs(x):
    x = np.asarray(x, dtype=np.float32)
    return (x - x.mean()) / (x.std() + 1e-6) if len(x) else x


def _moment_candidates(an, rules, t0, t1, k):
    """Sliding windows over a combined intensity curve (loudness + motion + cut density), 0.5 s steps."""
    lo, hi, n = rules["min_seconds"], rules["max_seconds"], max(rules["num_clips"], 1)
    target = rules.get("target_seconds") or 30
    span = t1 - t0
    length = max(lo, min(hi, target, int(span / n)))
    step = 0.5
    steps = int(span / step)
    en = np.array(an.d.get("energy") or [0.0])
    mo = np.array(an.d.get("motion") or [0.0])
    cut_arr = np.array(an.d["cuts"])
    curve = []
    for i in range(steps):
        a = t0 + i * step
        e_ = en[int(a / 0.1):int((a + step) / 0.1)]
        m_ = mo[int(a * 10):int((a + step) * 10)]
        c_ = ((cut_arr >= a) & (cut_arr < a + step)).sum() if len(cut_arr) else 0
        curve.append((e_.mean() if len(e_) else -60, m_.mean() if len(m_) else 0, c_))
    if not curve:
        return [{"start": t0, "end": t1, "title": "", "reason": "Whole range", "source": "whole"}]
    c = np.array(curve)
    intensity = _zs(c[:, 0]) + _zs(c[:, 1]) + 0.5 * _zs(c[:, 2])
    win = int(length / step)
    if len(intensity) <= win:
        return [{"start": t0, "end": t1, "title": "", "reason": "Whole range", "source": "whole"}]
    sums = np.convolve(intensity, np.ones(win), "valid")
    order = np.argsort(-sums)
    picks = []
    for i in order:
        s = t0 + i * step
        if all(abs(s - p["start"]) >= length * 0.6 for p in picks):
            e = min(t1, s + length)
            s, e = _snap_to_cuts(an, s, e, lo, hi)
            picks.append({"start": round(s, 2), "end": round(e, 2), "title": "",
                          "reason": "High combined loudness, motion and cut density", "source": "intensity"})
        if len(picks) >= k:
            break
    return picks


def _snap_to_cuts(an, s, e, lo, hi):
    """Nudge edges (≤1 s) onto camera cuts so clips start and end on clean shots."""
    cuts = an.d["cuts"]
    near = lambda t: min((c for c in cuts if abs(c - t) <= 1.0), key=lambda c: abs(c - t), default=t)
    s2, e2 = near(s), near(e)
    return (s2, e2) if lo - 0.5 <= e2 - s2 <= hi + 0.5 else (s, e)


def _dedupe_overlaps(cands):
    out = []
    for c in cands:
        if all(min(c["end"], k["end"]) - max(c["start"], k["start"]) < 0.8 * (c["end"] - c["start"]) for k in out):
            out.append(c)
    return out


def _pick_best(cands, n):
    kept = []
    for c in sorted(cands, key=lambda c: -(c["score"] or 0)):
        if all(min(c["end"], k["end"]) - max(c["start"], k["start"]) < 0.3 * (c["end"] - c["start"]) for k in kept):
            kept.append(c)
        if len(kept) == n:
            break
    return kept


# ---------- topic search ----------

def topic_search(tr, query, model=None, limit=12):
    """Find where a topic is discussed: exact keyword hits plus the model's semantic matches."""
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
    hits = []
    for s in tr["segments"]:
        txt = s["t"].lower()
        k = sum(txt.count(t) for t in terms)
        if k:
            hits.append({"start": s["s"], "end": s["e"], "text": s["t"], "why": f"mentions {', '.join(t for t in terms if t in txt)}",
                         "kind": "keyword", "rank": k})
    segs = tr["segments"]
    if segs:
        t, end_all = segs[0]["s"], segs[-1]["e"]
        while t < end_all:
            chunk = [s for s in segs if s["e"] > t and s["s"] < t + 600]
            if chunk:
                lines = "\n".join(f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}" for s in chunk)
                reply = ask_json(f"Find every passage in this transcript that is about: {query}\n"
                                 "Return start/end from the timestamps and one line on why it matches. Return none if nothing matches.\n\n"
                                 f"{lines}", SEARCH_SCHEMA, model, temperature=0)
                for m in reply.get("matches", []):
                    text = " ".join(s["t"] for s in segs if s["e"] > m["start"] and s["s"] < m["end"])
                    if text:
                        hits.append({"start": m["start"], "end": m["end"], "text": text[:300], "why": m["why"], "kind": "meaning", "rank": 2})
            t += 600
    hits.sort(key=lambda h: h["start"])
    merged = []
    for h in hits:
        if merged and h["start"] <= merged[-1]["end"] + 1:
            merged[-1]["end"] = max(merged[-1]["end"], h["end"])
            merged[-1]["rank"] += h["rank"]
            if h["kind"] == "meaning":
                merged[-1]["why"] = h["why"]
        else:
            merged.append(dict(h))
    return sorted(merged, key=lambda h: -h["rank"])[:limit]
