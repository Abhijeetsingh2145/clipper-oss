"""Remove burned-in captions from a video so it can be reused as clean B-roll or recaptioned.

Honest about what this is: there's no local video-inpainting model light enough to run well on
this Mac, so this isn't true AI inpainting. It uses ffmpeg's `delogo` filter over the caption
band (auto-guessed from edge-density, or a rectangle the user picks), which blends in pixels from
around the box. It works well for static, high-contrast caption bars; it leaves a soft smudge on
busy or moving backgrounds behind the text. The UI calls it "Caption remover (best effort)" so
that's never oversold.
"""
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .common import FFMPEG, FFPROBE, probe


def guess_band(path, sample_frames=12):
    """Looks for a horizontal band with unusually high edge density that stays in the same place
    across sampled frames -- the signature of a burned-in caption bar. Returns {x, y, w, h} in
    source pixels, defaulting to the bottom third (the most common caption placement) when no
    band stands out."""
    info = probe(path)
    w, h = info["width"], info["height"]
    dur = info["duration"]
    edge_sum = np.zeros(h, dtype=np.float64)
    n = 0
    for i in range(sample_frames):
        t = dur * (i + 0.5) / sample_frames
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "gray", "-vf", f"scale={w}:{h}", "-"],
            capture_output=True, check=True,
        ).stdout
        if len(out) != w * h:
            continue
        frame = np.frombuffer(out, dtype=np.uint8).reshape(h, w)
        edges = cv2.Canny(frame, 80, 160)
        edge_sum += edges.sum(axis=1)
        n += 1
    margin = 12
    fallback = {"x": margin, "y": int(h * 0.66), "w": w - margin * 2, "h": int(h * 0.30)}
    if n == 0:
        return fallback

    row_score = edge_sum / n
    bottom_half = row_score[h // 2:]
    if bottom_half.max() < bottom_half.mean() * 1.8:          # nothing stands out -> fall back
        return fallback
    peak = h // 2 + int(np.argmax(bottom_half))
    band_h = max(int(h * 0.08), 40)
    y0 = max(0, peak - band_h)
    y1 = min(h, peak + band_h * 2)
    return {"x": 0, "y": y0, "w": w, "h": y1 - y0}


def remove(path, out_path, band, fast=True):
    """Runs ffmpeg's delogo filter over `band` ({x,y,w,h}) and writes a clean copy to out_path.
    delogo needs margin around the box for its blend band, so a box touching (or the default
    guess going to) the frame edge is inset -- otherwise ffmpeg refuses with "outside of frame"."""
    info = probe(path)
    margin = 12
    x = max(margin, int(band["x"]))
    y = max(margin, int(band["y"]))
    bw = min(int(band["w"]), info["width"] - margin - x)
    bh = min(int(band["h"]), info["height"] - margin - y)
    if bw <= 0 or bh <= 0:
        raise ValueError("Caption band doesn't fit inside the frame")
    vf = f"delogo=x={x}:y={y}:w={bw}:h={bh}:show=0"
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(path), "-vf", vf,
           "-c:v", "libx264", "-preset", "fast" if fast else "slow", "-crf", "20",
           "-c:a", "copy", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path
