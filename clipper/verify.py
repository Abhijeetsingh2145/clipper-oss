"""Measure a rendered job: is every shot's sound in sync with the source, and does any frame next
to a join belong to the neighbouring shot (a stray or half-blended frame)?

    python -m clipper.verify jobs/<id>/out
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from . import spec as S
from .common import FFMPEG, open_frames, probe, read_exact

SR = 8000


def _pcm(path, ss=None, t=None):
    cmd = [FFMPEG, "-v", "error"] + (["-ss", f"{ss:.3f}"] if ss is not None else []) + ["-i", str(path)]
    cmd += (["-t", f"{t:.3f}"] if t else []) + ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)


def _env(x, hop=40):                      # 5 ms loudness envelope
    n = len(x) // hop
    e = np.log(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-9))
    return (e - e.mean()) / (e.std() + 1e-6)


def sync_offsets(sp, mp4):
    """Per shot: offset in ms between the clip's audio and the source at that shot (0 = in sync)."""
    clip = _pcm(mp4)
    pcs = S.timeline(sp)
    out = []
    for i, (s, e, o) in enumerate(pcs):
        d = e - s - (sp["ending"]["fade"] if i == len(pcs) - 1 else 0)          # ignore the fade-out
        if d < 0.4:
            continue
        a = _env(clip[int((o + 0.05) * SR): int((o + d - 0.05) * SR)])
        b = _env(_pcm(sp["source"], max(0.0, s - 0.25), d + 0.5))
        if len(b) <= len(a):
            continue
        best = max(range(len(b) - len(a)), key=lambda k: float(np.dot(a, b[k:k + len(a)])))
        out.append((round(s, 2), best * 5 - int(min(0.25, s) * 1000) - 50))
    return out


def _grab(path, t, w, h):
    p, hd = open_frames(path, f"scale={w}:{h}", max(0.0, t), 0.05)
    buf, _ = read_exact(p, w * h * 3, hd)
    p.kill()
    p.wait()
    return np.frombuffer(buf, np.uint8).reshape(h, w, 3) if len(buf) == w * h * 3 else None


def _hist(img):
    hsv = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    return cv2.normalize(h, h)


def stray_frames(sp, mp4):
    """Frames around each join that look more like the neighbouring source shot than their own."""
    fo = probe(mp4)["fps"]
    pcs = S.timeline(sp)
    dist = lambda a, b: cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA)
    center = lambda img: img[:, img.shape[1] // 3: 2 * img.shape[1] // 3]
    bad, checked = [], 0
    for (sa, ea, oa), (sb, eb, ob) in zip(pcs, pcs[1:]):
        jf = round(ob * fo)
        for k in (-2, -1, 0, 1):
            o = _grab(mp4, (jf + k - 0.3) / fo, 108, 192)          # seeking returns the first frame at/after t
            if o is None:
                continue
            own_t, stray_t = (ea + (k + 0.5) / fo, ea + 1 / fo + 1 / 60) if k < 0 else (sb + (k + 0.3) / fo, sb - 1 / 60 - 0.002)
            own, stray = (_grab(sp["source"], t, 192, 108) for t in (own_t, stray_t))
            if own is None or stray is None:
                continue
            h = _hist(o[40:150])
            checked += 1
            if dist(h, _hist(center(own))) > dist(h, _hist(center(stray))) + 0.05:
                bad.append((round(ob, 2), k))
    return checked, bad


if __name__ == "__main__":
    out = Path(sys.argv[1]).resolve()
    for p in sorted(out.glob("clip_*.json")):
        sp, mp4 = S.load(p), p.with_suffix(".mp4")
        if not mp4.exists():
            continue
        offs = sync_offsets(sp, mp4)
        worst = max((abs(x) for _, x in offs), default=0)
        checked, bad = stray_frames(sp, mp4)
        print(f"{p.stem}: {len(S.timeline(sp))} shots · sound offset worst {worst} ms "
              f"({sum(abs(x) <= 10 for _, x in offs)}/{len(offs)} within 10 ms) · "
              f"{len(bad)} of {checked} frames at joins look like the neighbouring shot {bad or ''}")
