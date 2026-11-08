"""Silence removal from the audio itself, not from transcript timestamps.

Whisper's word times are often 0.1-0.3 s off and stretch across pauses, so cutting the gaps
between words both misses real silences and clips word endings. Instead (method adapted from
Victoroak64/silence-cutter-premiere, MIT):
1. measure the level of the source audio every 10 ms;
2. auto-calibrate the threshold for THIS recording: noise floor = 10th percentile, speech level =
   90th percentile, threshold 30% of the way from noise to speech (kept >= 3 dB above the floor
   and >= 15 dB below speech, so the natural dips inside speech are never taken for pauses). If
   speech is not > 12 dB above the noise, the recording is too noisy to tell silence apart and
   the transcript-gap method is used instead;
3. a pause is any run below the threshold longer than the pause you allow;
4. margins are ASYMMETRIC: most of the kept pause stays after the speech ends (a breath, the
   word's tail) and little before the next line, which is what stops cuts sounding clipped;
5. dead air at the very start/end of a section is trimmed right up to the speech.
Clipper adds one safety rule: never cut where the transcript has a word, so quiet words survive.
"""
import subprocess
from functools import lru_cache

import numpy as np

from .common import FFMPEG

STEP = 0.01          # 10 ms analysis windows
SR = 16000
AFTER_SHARE = 0.85   # share of the kept pause placed after speech (0.12 s / 0.14 s in the original)


@lru_cache(maxsize=64)
def level_db(source, start, end):
    """dBFS of the source audio every 10 ms between start and end."""
    pcm = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source),
                          "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    hop = int(SR * STEP)
    n = len(a) // hop
    if not n:
        return np.zeros(0, np.float32)
    rms = np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(1))
    return 20 * np.log10(np.maximum(rms, 1e-7))


def calibrate(db):
    """Threshold between this recording's noise floor and speech level, or None if unreliable."""
    if len(db) < 50:
        return None
    noise, speech = float(np.percentile(db, 10)), float(np.percentile(db, 90))
    if speech - noise <= 12:
        return None
    thr = noise + 0.30 * (speech - noise)
    # A real pause sits >= 15 dB under speech. The original keeps 6 dB; in a clip that is nearly all
    # talking the 10th percentile lands on the dips INSIDE speech, and 6 dB would cut those dips.
    return min(max(thr, noise + 3), speech - 15), noise, speech


def find_silences(db, keep):
    """Runs below the threshold longer than `keep` seconds, as (start, end) offsets in seconds."""
    cal = calibrate(db)
    if cal is None:
        return [], None
    quiet = db < cal[0]
    runs, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            runs.append((i * STEP, j * STEP))
            i = j
        else:
            i += 1
    total = len(db) * STEP
    out = []
    for s, e in runs:
        at_start, at_end = s <= 0.35, e >= total - 0.35
        if at_start or at_end:                                   # dead air at the edges: trim to the speech
            if e - s >= 0.05:
                out.append((0.0 if at_start else s + keep * AFTER_SHARE, total if at_end else e - keep * (1 - AFTER_SHARE)))
        elif e - s > keep:
            out.append((s + keep * AFTER_SHARE, e - keep * (1 - AFTER_SHARE)))
    return [(a, b) for a, b in out if b - a >= 0.08], cal


def silence_cuts(source, seg_start, seg_end, words, keep=0.35):
    """Cuts (source seconds) that shorten every real pause to `keep` seconds, never touching a word.
    None when this audio is too noisy to calibrate (the caller then falls back to transcript gaps)."""
    db = level_db(str(source), round(seg_start, 3), round(seg_end, 3))
    found, cal = find_silences(db, keep)
    if cal is None:
        return None
    # Protect a transcribed word only where the audio is actually audible (> 6 dB over the noise
    # floor): Whisper stretches word times across silence (the first word often "starts" at 0.0),
    # so trusting its span would keep dead air; a genuinely quiet word still rises over the floor.
    audible = db > cal[1] + 6
    spans = []
    for w in words:
        i0, i1 = max(0, int((w["s"] - seg_start) / STEP)), min(len(db), int((w["e"] - seg_start) / STEP) + 1)
        hit = np.flatnonzero(audible[i0:i1])
        if len(hit):
            spans.append((seg_start + (i0 + hit[0]) * STEP - 0.04, seg_start + (i0 + hit[-1] + 1) * STEP + 0.04))
    spans.sort()
    total = len(db) * STEP
    cuts = []
    for a, b in found:
        # dead air reaching the end of the decoded audio runs to the section's end (decoders can
        # return a few ms less than asked)
        a, b = seg_start + a, (seg_end if b >= total - 1e-6 else seg_start + b)
        for ws, we in spans:                                     # carve out anything the transcript calls a word
            if we <= a or ws >= b:
                continue
            if ws - a >= 0.08:
                cuts.append([round(a, 3), round(ws, 3)])
            a = max(a, we)
        if b - a >= 0.08:
            cuts.append([round(a, 3), round(b, 3)])
    return cuts
