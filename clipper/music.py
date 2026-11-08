"""Music: the part of a song people replay is the hook / chorus. A song clip is ONE continuous
section (never re-ordered, never pause-trimmed) that starts and ends on a bar line, so it sounds
like the song and not like a cut.

1. Bars: every 4th beat of the detected beat grid, phase chosen where the onsets hit hardest
   (the downbeat).
2. Chorus: lines that repeat in the lyrics mark the chorus; without lyrics (or repeats), the
   loudest sustained stretch stands in for it.
3. Window: start on the bar at/before the first chorus line (with a little lead-in), extend bar by
   bar to the target length, end on a bar line. Sections from different choruses / peaks become
   the different clips.
"""
import re

import numpy as np


def bar_lines(an):
    beats = np.array(an.d.get("beats") or [])
    if len(beats) < 8:
        return beats
    onsets = np.array(an.d.get("onsets") or [])
    en = np.array(an.d.get("energy") or [])

    def strength(t):
        hit = float(np.min(np.abs(onsets - t))) < 0.07 if len(onsets) else False
        return (2.0 if hit else 0.0) + (en[int(t / 0.1)] if len(en) and int(t / 0.1) < len(en) else -60) / 60
    phase = max(range(4), key=lambda p: sum(strength(t) for t in beats[p::4]))
    return beats[phase::4]


def _chorus_starts(tr):
    norm = [(s, re.sub(r"[^\w ]", "", s["t"].lower()).strip()) for s in tr["segments"]]
    counts = {}
    for _, n in norm:
        if len(n.split()) >= 3:
            counts[n] = counts.get(n, 0) + 1
    rep = {n for n, k in counts.items() if k >= 2}
    starts, last = [], -1e9
    for s, n in norm:
        if n in rep and s["s"] - last > 20:                 # first line of each chorus occurrence
            starts.append(s["s"])
            last = s["s"]
        elif n in rep:
            last = s["s"]
    return starts


def song_sections(tr, an, rules, t0, t1, n):
    lo, hi = rules["min_seconds"], rules["max_seconds"]
    target = float(rules.get("target_seconds") or min(max(lo, 30), hi))
    bars = [b for b in bar_lines(an) if t0 <= b <= t1]
    en = np.array(an.d.get("energy") or [])
    loud = lambda a, b: float(np.mean(en[int(a / 0.1):int(b / 0.1)])) if len(en) and b > a else -60
    starts = [s for s in _chorus_starts(tr) if t0 <= s <= t1]
    how = "first line of a repeated chorus"
    if not starts:                                        # no lyrics to go on: loudest sustained stretches
        how = "loudest sustained stretch of the song"
        step = 2.0
        grid = [(loud(t, t + target), t) for t in np.arange(t0, max(t0 + 0.1, t1 - target), step)]
        grid.sort(reverse=True)
        for _, t in grid:
            if all(abs(t - s) > target for s in starts):
                starts.append(float(t))
            if len(starts) >= n * 2:
                break
    clips = []
    for st in starts:
        if bars:
            before = [b for b in bars if b <= st - 0.15]
            s = before[-1] if before else st
            ends = [b for b in bars if lo <= b - s <= hi]
            e = min(ends, key=lambda b: abs(b - s - target)) if ends else min(t1, s + target)
        else:
            s, e = max(t0, st - 0.3), min(t1, st - 0.3 + target)
        if any(min(e, c["end"]) - max(s, c["start"]) > 0.3 * (e - s) for c in clips):
            continue
        clips.append({"start": round(float(s), 3), "end": round(float(e), 3), "title": "", "source": "song",
                      "reason": f"Song section from the {how}, {e - s:.0f}s, starting and ending on a bar line"
                                + (" (beat grid)" if bars else "") + f" · {loud(s, e):.1f} dB average"})
        if len(clips) >= n:
            break
    return clips
