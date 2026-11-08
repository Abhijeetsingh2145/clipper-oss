"""Frame layouts: turn a source frame into an output frame of any aspect ratio.

Modes: track (follow the active speaker), fit (whole frame on blurred background), center,
manual (fixed crop position), split (two speakers stacked), gameplay (game + facecam),
screen (screen share + speaker), auto (picks one of these per camera shot).
"""
import cv2
import numpy as np

SAMPLE_STEP = 0.2


def _resize(img, w, h):
    interp = cv2.INTER_AREA if img.shape[1] > w else cv2.INTER_LANCZOS4
    return cv2.resize(img, (w, h), interpolation=interp)


def fit(img, ow, oh):
    h, w = img.shape[:2]
    s = min(ow / w, oh / h)
    fw, fh = max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)
    fg = _resize(img, fw, fh)
    if (fw, fh) == (ow, oh):
        return fg
    # blurred cover background, computed at low resolution for speed
    cs = max(ow / w, oh / h)
    small = cv2.resize(img, (max(2, int(w * cs / 8)), max(2, int(h * cs / 8))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    bg = cv2.resize(small, (int(w * cs) + 2, int(h * cs) + 2), interpolation=cv2.INTER_LINEAR)
    y0, x0 = (bg.shape[0] - oh) // 2, (bg.shape[1] - ow) // 2
    out = (bg[y0:y0 + oh, x0:x0 + ow] * 0.82).astype(np.uint8)
    oy, ox = (oh - fh) // 2, (ow - fw) // 2
    out[oy:oy + fh, ox:ox + fw] = fg
    return out


def crop_at(img, left_norm, ow, oh, top_norm=None, crop_h=None):
    """Crop a window with the output aspect ratio at a normalized left edge, then resize."""
    h, w = img.shape[:2]
    ch = crop_h or h
    cw = int(round(ch * ow / oh))
    if cw > w:                       # output wider than source: crop height instead
        cw, ch = w, int(round(w * oh / ow))
    x = int(round(min(max(left_norm, 0), 1) * (w - cw)))
    y = int(round(((h - ch) / 2) if top_norm is None else min(max(top_norm, 0), 1) * (h - ch)))
    return _resize(img[y:y + ch, x:x + cw], ow, oh)


def crop_width_norm(src_w, src_h, ow, oh):
    cw = min(src_h * ow / oh, src_w)
    return cw / src_w


class Framer:
    """Decides, for every output frame, how to compose it. Built once per clip render."""

    def __init__(self, spec, an, src_w, src_h, ow, oh, pieces):
        self.spec, self.an = spec, an
        self.sw, self.sh, self.ow, self.oh = src_w, src_h, ow, oh
        self.lay = spec["layout"]
        self.cwn = crop_width_norm(src_w, src_h, ow, oh)   # crop width as a fraction of source width
        self.wide_out = ow / oh >= src_w / src_h - 0.01     # output as wide as source: nothing to crop
        self.plans = []     # [(scene_start, scene_end, mode, times, lefts, extra)]
        for s, e, _ in pieces:
            for a, b in (an.scenes(s, e) if an else [(s, e)]):
                self.plans.append(self._plan_scene(a, b))

    # ---------- planning ----------
    def _scene_mode(self, s, e):
        mode = self._raw_mode(s, e)
        # inside a reel frame the picture must fill its box: never letterbox a second time
        return "center" if mode == "fit" and self.lay.get("fill") else mode

    def _raw_mode(self, s, e):
        mode = self.lay["mode"]
        if mode != "auto":
            return mode
        if self.wide_out:
            return "fit"
        rows = self.an.faces_between(s, e) if self.an else []
        with_face = [r for r in rows if r["d"]]
        if not rows or len(with_face) / len(rows) < 0.5:
            return "fit"
        # faces too small to crop around (wide shots, crowds) look better uncropped
        biggest = np.median([max(d[2] for d in r["d"]) for r in with_face])
        if biggest < 0.045:
            return "fit"
        speakers = self._speaker_count(with_face)
        if speakers >= 2 and self.lay.get("switching", True) and self._two_far_apart(with_face):
            return "split"
        return "track"

    def _speaker_count(self, rows):
        counts = {}
        for r in rows:
            for d in r["d"]:
                counts[d[5]] = counts.get(d[5], 0) + 1
        return sum(1 for c in counts.values() if c >= 0.5 * len(rows))

    def _two_far_apart(self, rows):
        """Split screen only when both faces can't fit in one vertical crop."""
        gaps = []
        for r in rows:
            if len(r["d"]) >= 2:
                xs = sorted(d[0] + d[2] / 2 for d in r["d"])
                gaps.append(xs[-1] - xs[0])
        return bool(gaps) and np.median(gaps) > self.cwn * 0.8

    def _active_choice(self, rows, i, current):
        """Pick who to follow at sample i: the face whose mouth moves most (with hysteresis)."""
        window = rows[max(0, i - 4): i + 5]            # ~±0.8 s at 5 fps
        act, seen = {}, {}
        for r in window:
            for d in r["d"]:
                act[d[5]] = act.get(d[5], 0) + d[6]
                seen[d[5]] = seen.get(d[5], 0) + 1
        here = {d[5]: d for d in rows[i]["d"]}
        if not here:
            return current
        target_x = self.lay.get("target_x")
        if target_x is not None:                       # manual tracking: follow the face nearest a chosen spot
            return min(here, key=lambda k: abs(here[k][0] + here[k][2] / 2 - target_x))
        score = {k: act.get(k, 0) / max(seen.get(k, 1), 1) for k in here}
        best = max(score, key=score.get)
        if current in here and score[best] < 1.35 * score.get(current, 0) + 0.02:
            return current
        if score[best] < 0.05:                         # nobody clearly talking: biggest face
            return max(here, key=lambda k: here[k][2])
        return best

    def _plan_scene(self, s, e):
        mode = self._scene_mode(s, e)
        if mode in ("fit", "center", "manual") or not self.an:
            return (s, e, mode if self.an or mode != "track" else "center", None, None, None)
        rows = self.an.faces_between(s, e)
        if mode == "split":
            return (s, e, "split", None, None, self._split_plan(rows))
        if mode in ("gameplay", "screen"):
            return (s, e, mode, None, None, self._facecam_box(rows))
        # track
        times, centers, current = [], [], None
        for i, r in enumerate(rows):
            current = self._active_choice(rows, i, current)
            d = next((d for d in r["d"] if d[5] == current), None)
            times.append(r["t"])
            centers.append(d[0] + d[2] / 2 if d else None)
        if not any(c is not None for c in centers):
            return (s, e, "center" if self.lay.get("fill") else "fit", None, None, None)
        # fill gaps, then smooth with a dead zone; snap at the start of every shot
        first = next(c for c in centers if c is not None)
        filled, last = [], first
        for c in centers:
            last = c if c is not None else last
            filled.append(last)
        cur, lefts = filled[0], []
        for i, c in enumerate(filled):
            if i and abs(c - cur) > 0.25:              # speaker switch: cut straight to them
                cur = c
            elif abs(c - cur) > 0.025:
                cur += 0.22 * (c - cur)
            lefts.append((cur - self.cwn / 2) / max(1e-6, 1 - self.cwn))
        return (s, e, "track", np.array(times), np.array(lefts), None)

    def _split_plan(self, rows):
        """Two most-present faces: top = left person, bottom = right person."""
        pos = {}
        for r in rows:
            for d in r["d"]:
                pos.setdefault(d[5], []).append(d[0] + d[2] / 2)
        ids = sorted(pos, key=lambda k: -len(pos[k]))[:2]
        xs = sorted(float(np.median(pos[k])) for k in ids)
        return {"xs": xs}

    def _facecam_box(self, rows):
        fc = self.lay.get("facecam")
        if fc:
            return {"box": fc}
        # a small face that sits in the same corner through the whole shot is a webcam overlay
        boxes = [d for r in rows for d in r["d"] if d[2] < 0.2]
        if len(boxes) < 0.4 * max(len(rows), 1):
            return {"box": None}
        x, y, w, h = (float(np.median([b[k] for b in boxes])) for k in range(4))
        pad = 1.2
        return {"box": [max(0, x - w * pad), max(0, y - h * pad * 0.8), min(1, w * (1 + 2 * pad)), min(1, h * (1 + 2 * pad))]}

    # ---------- per frame ----------
    def _plan_for(self, t):
        for p in self.plans:
            if p[0] - 1e-3 <= t <= p[1] + 1e-3:
                return p
        return self.plans[-1]

    def compose(self, img, t):
        s, e, mode, times, lefts, extra = self._plan_for(t)
        ow, oh = self.ow, self.oh
        if mode == "fit":
            return fit(img, ow, oh)
        if mode == "center":
            return crop_at(img, 0.5, ow, oh)
        if mode == "manual":
            return crop_at(img, self.lay.get("manual_x", 0.5), ow, oh)
        if mode == "track":
            return crop_at(img, float(np.interp(t, times, lefts)), ow, oh)
        if mode == "split":
            xs = extra["xs"]
            half = oh // 2
            top = crop_at(img, _left_for(xs[0], img, ow, half), ow, half)
            bottom = crop_at(img, _left_for(xs[-1], img, ow, oh - half), ow, oh - half)
            return np.vstack([top, bottom])
        if mode in ("gameplay", "screen"):
            box = extra["box"]
            if not box:
                return fit(img, ow, oh)
            h, w = img.shape[:2]
            bx, by, bw, bh = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
            cam = img[by:by + bh, bx:bx + bw]
            cam_h = int(oh * (0.38 if mode == "gameplay" else 0.42))
            main_h = oh - cam_h
            main = crop_at(img, 0.5, ow, main_h) if mode == "gameplay" else fit(img, ow, main_h)
            camf = crop_at(cam, 0.5, ow, cam_h) if cam.size else np.zeros((cam_h, ow, 3), np.uint8)
            return np.vstack([main, camf])
        return fit(img, ow, oh)


def _left_for(center_x, img, ow, oh):
    h, w = img.shape[:2]
    cw = min(h * ow / oh, w) / w
    return (center_x - cw / 2) / max(1e-6, 1 - cw)
