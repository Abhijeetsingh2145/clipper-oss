"""Montage editing for trailers, gameplay, music videos, sports: the best MOMENTS from across the
whole video, cut together where the source itself cuts. Every shot keeps its own sound.

How a montage is built (all measured, nothing random):
1. The video is split into its real shots, using camera cuts located to the exact frame
   (analysis._refine_cuts). A moment is one or more consecutive whole shots, 1.2-5 s long, so
   every cut in our edit happens exactly where the source's picture changes: no half-shots and
   no stray frame of the neighbouring shot. A shot longer than that (gameplay, long takes) may
   be entered or left on a sound hit (an audio onset) instead.
2. A moment never starts or ends inside a spoken word or line of dialogue.
3. Every moment is scored against the rest of this video: loudness + motion + image detail +
   faces, minus black frames / fades, plus a bonus when its cut lands on a sound hit or beat.
   Scores are relative, so a calm vlog and a loud trailer each get their own best moments.
4. Each clip takes the best moments no other clip used (clips never share footage) until it
   reaches the target length. It opens on the moment with the strongest first second (the hook),
   plays the rest in story order, and finishes on the highest-scoring moment.
5. Each moment plays with its own original audio, with a few-ms fade at every join so there are
   no clicks. The score reports, measured: how many joins sit on real camera cuts, how many land
   on a sound hit or beat, and whether any join chops a word. The clip fades out at the end.
"""
import numpy as np

MIN_MOMENT = 1.2
OUT_FPS = 30


def _z(x):
    x = np.asarray(x, dtype=np.float32)
    return (x - x.mean()) / (x.std() + 1e-6) if len(x) else x


def _mean(a, default=0.0):
    return float(np.mean(a)) if len(a) else default


def _speech_safe(an, t):
    """True when t is between words and between spoken sentences (never cut someone off)."""
    if any(w["s"] + 0.02 < t < w["e"] - 0.02 for w in an.tr["words"] if abs(w["s"] - t) < 3):
        return False
    if not hasattr(an, "_sentences"):
        an._sentences = dialogue_lines(an, 0, float("inf"))
    return not any(g["s"] + 0.15 < t < g["e"] - 0.15 for g in an._sentences)


def _hits(an):
    return np.array(sorted(set(an.d.get("onsets") or []) | set(an.d.get("beats") or [])))


def _on_hit(hits, t, tol=0.06):
    return bool(len(hits)) and float(np.min(np.abs(hits - t))) <= tol


def cut_points(an, t0, t1, max_len):
    """Where an edit may cut: real camera cuts (exact frames), plus sound hits inside shots that
    are too long to use whole. Returns sorted [(leave_at, enter_at, kind)], kind 'cut'/'hit'/'edge':
    a moment ending here ends at leave_at (before any transition starts), one starting here starts
    at enter_at (once the new shot has settled; the same frame for a hard cut)."""
    ins = an.d.get("cuts_in") or an.d["cuts"]
    cuts = [(c, i) for c, i in zip(an.d["cuts"], ins) if t0 + 0.05 < c and i < t1 - 0.05]
    pts = [(t0, t0, "edge")] + [(c, i, "cut") for c, i in cuts] + [(t1, t1, "edge")]
    hits = _hits(an)
    out = []
    for (a, ai, ka), (b, _, _) in zip(pts, pts[1:]):
        out.append((a, ai, ka))
        if b - ai > max_len:                                  # long take: allow cuts on sound hits inside it
            last = ai
            for h in hits:
                if ai + MIN_MOMENT <= h <= b - MIN_MOMENT and h - last >= MIN_MOMENT:
                    out.append((round(float(h), 4), round(float(h), 4), "hit"))
                    last = h
    out.append(pts[-1])
    return [p for p in out if p[2] == "edge" or (_speech_safe(an, p[0]) and _speech_safe(an, p[1]))]


def candidate_moments(an, t0, t1, max_len):
    pts = cut_points(an, t0, t1, max_len)
    hits = _hits(an)
    spans = []
    for i, (_, s, ks) in enumerate(pts):
        for e, _, ke in pts[i + 1:]:
            if e - s > max_len:
                break
            if e - s >= MIN_MOMENT:
                spans.append((s, e, ks, ke))
    rows = []
    for s, e, ks, ke in spans:
        en = [x for x in an.energy_between(s, e) if x > -60]
        lu = np.array(an.luma_between(s, e) or [0.5])
        first = np.array(an.luma_between(s, s + 1.0) or [0.5])
        dark_share = float((lu < 0.05).mean())
        # a flash to black reads as huge "motion", so the hook only counts a fully lit first second
        hook = _mean(an.motion_between(s, s + 1.0)) if (first >= 0.05).all() else 0.0
        rows.append((s, e, _mean(en, -60), _mean(an.motion_between(s, e)), _mean(an.detail_between(s, e)),
                     an.face_share(s, e), float(lu.mean()), dark_share, hook, float(_on_hit(hits, s))))
    if not rows:
        return []
    f = np.array(rows)
    score = _z(f[:, 2]) + _z(f[:, 3]) + 0.3 * _z(np.log1p(f[:, 4])) + 0.4 * f[:, 5] + 0.3 * f[:, 9]
    score -= 3.0 * (f[:, 6] < 0.07) + 6.0 * f[:, 7]                # dark moment / share of black or faded frames
    return [{"start": round(r[0], 4), "end": round(r[1], 4), "start_kind": sp[2], "end_kind": sp[3],
             "score": float(sc), "hook": float(r[8]),
             "dark": bool(r[6] < 0.07 or r[7] > 0.05)} for r, sc, sp in zip(rows, score, spans)]   # >5% black: a fade


def _overlaps(a, b):
    return a["start"] < b["end"] - 0.01 and b["start"] < a["end"] - 0.01


LINE_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"}, "hook": {"type": "integer"}, "payoff": {"type": "integer"}},
        "required": ["i", "hook", "payoff"]}}},
    "required": ["lines"],
}


def dialogue_lines(an, t0, t1):
    """Spoken SENTENCES inside the range (Whisper often groups several into one segment; a single
    punchy sentence makes a better hook than a 12 s speech), from the word timings."""
    out, cur = [], []
    for w in an.tr["words"]:
        if not (t0 <= w["s"] and w["e"] <= t1):
            continue
        if cur and w["s"] - cur[-1]["e"] > 1.2:                       # long silence also ends a sentence
            out.append(cur)
            cur = []
        cur.append(w)
        if w["w"].rstrip('"\'').endswith((".", "!", "?")) and not w["w"].lower().startswith(("mr.", "mrs.", "dr.", "st.")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    merged = []
    for ws in out:                                                    # fold one-word fragments into a neighbour
        if merged and (len(ws) < 2 or ws[-1]["e"] - ws[0]["s"] < 0.6) and ws[0]["s"] - merged[-1][-1]["e"] < 0.4:
            merged[-1] = merged[-1] + ws
        else:
            merged.append(ws)
    return [{"s": ws[0]["s"], "e": ws[-1]["e"], "t": " ".join(w["w"] for w in ws).strip()}
            for ws in merged if len(ws) >= 2]


def rank_lines(an, lines, model=None, focus=""):
    """How well each spoken line works as the OPENING hook and as the FINAL payoff (0-3 each).
    The language model judges the words; without it, a measured fallback uses punctuation
    (! and ? lines land harder), brevity and how loud the line is relative to the video."""
    if not lines:
        return []
    numbered = "\n".join(f"{i}. {g['t'].strip()}" for i, g in enumerate(lines))
    try:
        from .llm import ask_json
        out = ask_json(
            "These are the spoken lines of a video, in order. A short-form editor will re-cut them into a "
            "30-60 s vertical clip. Rate EVERY line 0-3:\n"
            "- hook: as the very FIRST thing a scroller hears, does it stop the scroll on its own "
            "(bold claim, threat, striking introduction, question, conflict)? 0 = needs context.\n"
            "- payoff: as the LAST line of the clip, does it land (punchline, declaration, climax, "
            "resolution)? 0 = sounds unfinished or like a setup.\n"
            + (f"Campaign focus: {focus}\n" if focus else "") + f"\nLINES:\n{numbered}",
            LINE_SCHEMA, model, temperature=0)
        by = {int(r["i"]): r for r in out["lines"] if 0 <= int(r["i"]) < len(lines)}
        if len(by) >= len(lines) * 0.8:
            return [(float(min(3, max(0, by.get(i, {}).get("hook", 0)))), float(min(3, max(0, by.get(i, {}).get("payoff", 0)))),
                     "AI") for i in range(len(lines))]
    except Exception:
        pass
    loud = [_mean([x for x in an.energy_between(g["s"], g["e"]) if x > -60], -60) for g in lines]
    rank = np.argsort(np.argsort(loud)) / max(1, len(lines) - 1)        # 0..1, loudest = 1
    out = []
    for g, r in zip(lines, rank):
        t = g["t"].strip()
        bang, short = t.endswith("!") or t.endswith("?"), len(t.split()) <= 12
        out.append((1.5 * bang + 0.75 * short + 0.75 * r, 1.5 * bang + 1.5 * r, "measured"))
    return out


def line_moments(an, lines, ranks, pts):
    """Each spoken line as an edit unit: from the camera cut just before it to the camera cut just
    after it, when those are close; otherwise from a word gap with a short breath of air."""
    words = an.tr["words"]
    out = []
    for g, (hook, payoff, by) in zip(lines, ranks):
        ls, le = g["s"], g["e"]
        prev_end = max([w["e"] for w in words if w["e"] <= ls - 0.01] or [ls - 1.0])
        next_start = min([w["s"] for w in words if w["s"] >= le + 0.01] or [le + 1.0])
        before = [p for p in pts if prev_end - 0.01 <= p[1] <= ls - 0.03 and p[1] >= ls - 2.0]
        after = [p for p in pts if le + 0.03 <= p[0] <= next_start + 0.01 and p[0] <= le + 1.5]
        if before:
            s, sk = before[-1][1], before[-1][2]
        else:
            s, sk = max(prev_end + 0.02, ls - 0.12), "speech"
        if after:
            e, ek = after[0][0], after[0][2]
        else:
            e, ek = min(next_start - 0.02, le + 0.3), "speech"
        if e - s < 0.6:
            continue
        out.append({"start": round(s, 4), "end": round(e, 4), "start_kind": sk, "end_kind": ek, "line": g["t"].strip(),
                    "hook_rank": hook, "payoff_rank": payoff, "ranked_by": by,
                    "hook": _mean(an.motion_between(s, s + 1.0)), "dark": False})
    return out


def frame_safe(m, src_fps):
    """Segment edges a hair inside the shot, so the decoder never shows a frame of the neighbour:
    seeking returns the first frame at/after the start; the last output frame must stay before
    the next shot's first frame."""
    s = m["start"] - (0.2 / src_fps if m["start_kind"] == "cut" else 0)
    e = m["end"] - (1.0 / OUT_FPS if m["end_kind"] == "cut" else 0)
    return {"start": round(max(0.0, s), 4), "end": round(e, 4)}


def build(an, rules, t0, t1, n, target=None, model=None, focus=""):
    """Return up to n edited clips.

    Without dialogue: the best visual moments (see module doc).
    With dialogue (trailers, character spots, ads): every spoken line is kept whole and becomes an
    edit unit; the clip OPENS on the line ranked best as a hook, CLOSES on the line ranked best as a
    payoff, and the middle mixes the strongest other lines with the strongest action shots, in story
    order. Each further clip opens and closes on different lines. Clips share no footage when the
    video is long enough; a short source (a 1-minute trailer) may reuse shots, but never the same
    opening or ending."""
    lo, hi = rules["min_seconds"], rules["max_seconds"]
    target = float(target or rules.get("target_seconds") or min(max(lo, 30), hi))
    target = max(lo, min(hi, target))
    max_len = float(np.clip(target / 6, 2.5, 5.0))
    src_fps = an.d.get("fps") or 30
    pts = cut_points(an, t0, t1, max_len)
    lines = dialogue_lines(an, t0, t1)
    ranks = rank_lines(an, lines, model, focus)
    talk = line_moments(an, lines, ranks, pts)
    action = [m for m in candidate_moments(an, t0, t1, max_len) if not m["dark"]
              and not any(_overlaps(m, l) for l in talk)]                 # lines are placed as whole units
    for m in action:
        m["kind"] = "action"
    if talk:
        z = _z([l["hook_rank"] + l["payoff_rank"] for l in talk])
        for l, v in zip(talk, z):
            l["kind"] = "line"
            l["score"] = 1.0 + float(v)                                   # a good line beats an average shot
    pool = sorted(talk + action, key=lambda m: -m["score"])
    usable, reach = 0.0, -1.0                                             # footage covered (candidates overlap)
    for s, e in sorted((m["start"], m["end"]) for m in pool):
        usable += max(0.0, e - max(s, reach))
        reach = max(reach, e)
    share = usable < n * target * 1.3                                     # short source: allow reuse
    taken, openers, closers, clips = [], [], [], []
    for _ in range(n):
        blocked = [] if share else taken
        free = [m for m in talk if not any(_overlaps(m, x) for x in blocked)]
        opener = max((m for m in free if m not in openers and m not in closers),
                     key=lambda m: (m["hook_rank"], m["score"]), default=None)
        closer = max((m for m in free if m is not opener and m not in closers and m not in openers),
                     key=lambda m: (m["payoff_rank"], m["score"]), default=None)
        chosen = [m for m in (opener, closer) if m]
        total = sum(m["end"] - m["start"] for m in chosen)
        for m in pool:
            if total >= target:
                break
            length = m["end"] - m["start"]
            if m in chosen or total + length > hi or any(_overlaps(m, x) for x in blocked + chosen):
                continue
            chosen.append(m)
            total += length
        if len(chosen) < 2 or total < lo - 0.5:
            break
        taken += chosen
        if opener is None:                                                # no dialogue: strongest first second
            closer = max(chosen, key=lambda m: m["score"])
            opener = max((m for m in chosen if m is not closer), key=lambda m: m["hook"])
        elif closer is None:
            closer = max((m for m in chosen if m is not opener), key=lambda m: m["score"])
        openers.append(opener)
        closers.append(closer)
        middle = sorted((m for m in chosen if m is not opener and m is not closer), key=lambda m: m["start"])
        order = [opener, *middle, closer]
        segs = [frame_safe(m, src_fps) for m in order]
        n_lines = sum(1 for m in order if m.get("kind") == "line")
        clips.append({
            "start": segs[0]["start"], "end": segs[-1]["end"], "segments": segs,
            "moments": [{k: m.get(k) for k in ("start", "end", "start_kind", "end_kind", "kind", "line")} for m in order],
            "moment_scores": [round(m["score"], 3) for m in order],
            "opening_line": opener.get("line"), "closing_line": closer.get("line"),
            "opening_hook_rank": opener.get("hook_rank"), "closing_payoff_rank": closer.get("payoff_rank"),
            "ranked_by": ranks[0][2] if ranks else None,
            "title": "", "source": "montage",
            "reason": (f"Edit of {len(order)} moments ({n_lines} spoken lines kept whole, {len(order) - n_lines} action shots): "
                       + (f"opens on “{opener['line'][:60]}”, ends on “{closer['line'][:60]}”"
                          f" (hook/payoff ranked by {'the AI model' if ranks and ranks[0][2] == 'AI' else 'loudness + punctuation'})"
                          if opener.get("line") and closer.get("line") else "opens on the strongest hook, ends on the strongest moment")
                       + "; every cut on a camera cut or a gap between words, every shot with its own sound"),
        })
    return clips


def cut_report(c, an):
    """Measured quality of every join in the montage."""
    moments = c.get("moments") or [{**s, "start_kind": "?", "end_kind": "?"} for s in c["segments"]]
    hits = _hits(an)
    near = lambda arr, t: bool(len(arr)) and float(np.min(np.abs(np.array(arr) - t))) <= 0.02
    # clean = on a camera cut, a sound hit, or (for dialogue) the silence between two words
    ok_out = lambda m: near(an.d["cuts"], m["end"]) or m["end_kind"] in ("hit", "edge", "speech")
    ok_in = lambda m: near(an.d.get("cuts_in") or an.d["cuts"], m["start"]) or m["start_kind"] in ("hit", "edge", "speech")
    joins = list(zip(moments, moments[1:]))
    return {
        "joins": len(joins),
        "clean": sum(ok_out(a) and ok_in(b) for a, b in joins),
        "word_gap": sum(a["end_kind"] == "speech" or b["start_kind"] == "speech" for a, b in joins),
        "on_hit": sum(_on_hit(hits, b["start"]) for _, b in joins),
        "chopped": sum(not _speech_safe(an, a["end"]) or not _speech_safe(an, b["start"]) for a, b in joins),
    }


def score_montage(c, an, rules, stats):
    """Quality estimate for a montage, from the same kinds of measurements as single clips."""
    from .scoring import LABELS, _band, _lin, _pct_rank, weights
    segs = c["segments"]
    scores = c.get("moment_scores") or [0] * len(segs)
    total = sum(s["end"] - s["start"] for s in segs)
    heard = [x for s in segs for x in an.energy_between(s["start"], s["end"]) if x > -60]   # what's heard, in order
    ranks = [_pct_rank(stats["motion_sorted"], _mean(an.motion_between(s["start"], s["end"]))) for s in segs]
    parts = {}
    hook_rank = _pct_rank(stats["motion_sorted"], _mean(an.motion_between(segs[0]["start"], segs[0]["start"] + 1)))
    loud_diff = (_mean(heard[:30]) - float(np.median(heard))) if len(heard) > 35 else 0.0
    parts["hook"] = (0.6 * hook_rank + 0.4 * _lin(loud_diff, -6, 3),
                     f"First second's motion at the {hook_rank:.0f}th percentile of this video · audio starts {loud_diff:+.1f} dB vs the rest")
    if c.get("opening_line") and c.get("opening_hook_rank") is not None:      # opens on a spoken line
        parts["hook"] = (0.5 * parts["hook"][0] + 0.5 * 100 * c["opening_hook_rank"] / 3,
                         f"Opens on “{c['opening_line'][:70]}” (hook {c['opening_hook_rank']:.0f}/3 by "
                         f"{'the AI model' if c.get('ranked_by') == 'AI' else 'loudness + punctuation'}) · " + parts["hook"][1])
    r = cut_report(c, an)
    j = max(r["joins"], 1)
    after = [x for x in an.energy_between(segs[-1]["end"], segs[-1]["end"] + 0.8) if x > -60]
    drop = (_mean(heard[-10:]) - _mean(after)) if after else 6.0
    parts["self_contained"] = (60 * r["clean"] / j + 25 * r["on_hit"] / j + 15 * (_lin(drop, -2, 6) / 100) - 30 * r["chopped"],
                               f"{r['clean']}/{r['joins']} joins clean ({r['joins'] - r['word_gap']} on camera cuts or sound hits, {r['word_gap']} in gaps between words) · "
                               f"{r['on_hit']}/{r['joins']} land on a sound hit or beat · {r['chopped']} chop a word"
                               f" · ends with the sound {'falling' if drop > 0 else 'still going'} ({drop:+.1f} dB), then fades out")
    place = 1 + sum(1 for x in scores if x > scores[-1])
    last_rank = 100 * (len(segs) - place) / max(1, len(segs) - 1)
    climax = (max(heard[int(len(heard) * 2 / 3):], default=-60) - float(np.median(heard))) if heard else 0
    parts["payoff"] = (0.5 * last_rank + 0.5 * _lin(climax, 0, 8),
                       f"Final moment is the #{place} strongest of {len(segs)} · last third peaks {climax:+.1f} dB")
    if c.get("closing_line") and c.get("closing_payoff_rank") is not None:    # ends on a spoken line
        parts["payoff"] = (0.4 * parts["payoff"][0] + 0.6 * 100 * c["closing_payoff_rank"] / 3,
                           f"Ends on “{c['closing_line'][:70]}” (payoff {c['closing_payoff_rank']:.0f}/3) · " + parts["payoff"][1])
    inner = sum(len(an.cuts_between(s["start"] + 0.1, s["end"] - 0.1)) for s in segs)
    per10 = (len(segs) - 1 + inner) / max(total, 0.1) * 10
    parts["pacing"] = (_band(per10, 0.5, 1.5, 9.0, 14.0), f"{len(segs)} moments · {per10:.1f} visible cuts per 10 s (incl. the source's own)")
    dark = sum(1 for s in segs if _mean(an.luma_between(s["start"], s["end"]), 0.5) < 0.07)
    parts["visual"] = (max(0.0, _mean(ranks) - 25 * dark), f"Moments average the {_mean(ranks):.0f}th motion percentile of this video"
                       + (f" · {dark} dark moment(s)" if dark else ""))
    std = float(np.std(heard)) if len(heard) > 5 else 0
    parts["audio"] = (_band(std, 0.5, 3.0, 9.0, 16.0), f"Loudness varies by {std:.1f} dB")
    in_len = rules["min_seconds"] - 1 <= total <= rules["max_seconds"] + 1
    parts["fit"] = (70.0 if in_len else 35.0, "Visual montage: judged on structure, not words" + ("" if in_len else f" · length {total:.0f}s outside the brief"))
    parts = {k: (float(np.clip(v, 0, 100)), ev) for k, (v, ev) in parts.items()}
    w, n_cal = weights()
    tot = sum(w[k] * parts[k][0] for k in parts) / sum(w[k] for k in parts)
    return {"total": int(round(tot)),
            "parts": {k: {"label": LABELS[k], "score": int(round(v)), "weight": w[k], "evidence": ev} for k, (v, ev) in parts.items()},
            "kind": "montage", "calibrated_on": n_cal,
            "note": "Clip quality estimate, not a view prediction" + (f" · weights calibrated on {n_cal} of your posted clips" if n_cal else "")}
