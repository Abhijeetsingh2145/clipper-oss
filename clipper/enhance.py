"""Per-frame visual effects on the output timeline: zooms, jump-cut punch-ins, B-roll cutaways,
image/logo overlays, emoji pops, sharpening. Plus intro/outro concatenation."""
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .common import FFMPEG
from .layouts import crop_at, fit

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"


def _ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def zoom(img, scale, cx=0.5, cy=0.45):
    if scale <= 1.001:
        return img
    h, w = img.shape[:2]
    cw, ch = int(w / scale), int(h / scale)
    x = int(min(max(cx * w - cw / 2, 0), w - cw))
    y = int(min(max(cy * h - ch / 2, 0), h - ch))
    return cv2.resize(img[y:y + ch, x:x + cw], (w, h), interpolation=cv2.INTER_LINEAR)


@lru_cache(maxsize=64)
def emoji_rgba(ch, size):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(EMOJI_FONT, 160)
    im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20, 20), ch, font=font, embedded_color=True)
    bbox = im.getbbox()
    if not bbox:
        return None
    im = im.crop(bbox).resize((size, size))
    arr = np.array(im)
    return arr[:, :, [2, 1, 0, 3]]                      # RGBA -> BGRA


@lru_cache(maxsize=16)
def load_rgba(path, max_w):
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.shape[2] == 3:
        img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    s = max_w / img.shape[1]
    return cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=cv2.INTER_AREA)


def blend(dst, rgba, x, y, opacity=1.0):
    h, w = rgba.shape[:2]
    H, W = dst.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return dst
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = (src[:, :, 3:4].astype(np.float32) / 255) * opacity
    roi = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (roi * (1 - a) + src[:, :, :3].astype(np.float32) * a).astype(np.uint8)
    return dst


class _BrollReader:
    def __init__(self, item, ow, oh, fps):
        self.item, self.ow, self.oh, self.fps = item, ow, oh, fps
        self.cap = None
        self.still = None
        p = item["path"]
        if item.get("kind") == "image" or Path(p).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            img = cv2.imread(str(p))
            self.still = img
        else:
            self.cap = cv2.VideoCapture(str(p))
            self.cap.set(cv2.CAP_PROP_POS_MSEC, float(item.get("from", 0)) * 1000)
        self.last = None

    def frame(self, k_t):
        if self.still is not None:                       # slow Ken Burns push-in on stills
            return zoom(crop_at(self.still, 0.5, self.ow, self.oh), 1.0 + 0.06 * min(k_t / max(self.item["dur"], 0.1), 1))
        ok, f = self.cap.read() if self.cap else (False, None)
        if ok:
            self.last = crop_at(f, 0.5, self.ow, self.oh) if f.shape[1] / f.shape[0] < self.ow / self.oh * 1.9 else fit(f, self.ow, self.oh)
        return self.last


class Effects:
    def __init__(self, spec, out_words, total, ow, oh, fps, pieces):
        self.spec, self.ow, self.oh, self.fps, self.total = spec, ow, oh, fps, total
        self.zooms = spec.get("zooms", [])
        # jump cuts = boundaries between consecutive pieces that came from the same segment
        self.piece_starts = [o for _, _, o in pieces]
        self.punch = spec.get("transitions") == "punch"
        self.broll = sorted(spec.get("broll", []), key=lambda b: b["t"])
        self.readers = {}
        logo = spec.get("logo", {})
        self.logo = load_rgba(logo["path"], int(ow * float(logo.get("scale", 0.16)))) if logo.get("path") else None
        self.logo_cfg = logo
        self.overlays = [o for o in spec.get("overlays", []) if o.get("type") == "image" and o.get("path")]
        self.emojis = []
        if spec["captions"].get("emoji"):
            for w in out_words:
                if w.get("emoji"):
                    self.emojis.append((w["s"], min(w["s"] + 1.4, self.total), w["emoji"]))
        self.sharpen = spec.get("sharpen")
        self.to_black = min(float((spec.get("ending") or {}).get("to_black") or 0), total / 3)

    def apply(self, img, t):
        # zoom (keyframed ease in/out) or punch-in on alternate jump-cut pieces
        scale = 1.0
        for z in self.zooms:
            if z["t"] <= t <= z["t"] + z["dur"]:
                ramp = min(0.25, z["dur"] / 3)
                k = _ease((t - z["t"]) / ramp) * _ease((z["t"] + z["dur"] - t) / ramp)
                scale = max(scale, 1 + (float(z.get("scale", 1.15)) - 1) * k)
        if self.punch and len(self.piece_starts) > 1:
            idx = max(i for i, o in enumerate(self.piece_starts) if o <= t + 1e-6)
            if idx % 2 == 1:
                scale = max(scale, 1.08)
        img = zoom(img, scale)

        for i, b in enumerate(self.broll):
            if b["t"] <= t < b["t"] + b["dur"]:
                r = self.readers.get(i) or self.readers.setdefault(i, _BrollReader(b, self.ow, self.oh, self.fps))
                f = r.frame(t - b["t"])
                if f is not None:
                    fade = min(1.0, (t - b["t"]) / 0.15, (b["t"] + b["dur"] - t) / 0.15)
                    img = f if fade >= 1 else cv2.addWeighted(f, fade, img, 1 - fade, 0)

        for o in self.overlays:
            if o["t"] <= t <= o["t"] + o.get("dur", self.total):
                rgba = load_rgba(o["path"], int(self.ow * float(o.get("scale", 0.4))))
                if rgba is not None:
                    blend(img, rgba, int(o.get("x", 0.5) * self.ow - rgba.shape[1] / 2), int(o.get("y", 0.3) * self.oh - rgba.shape[0] / 2))

        for t0, t1, ch in self.emojis:
            if t0 <= t <= t1:
                size = int(self.ow * 0.16)
                pop = 0.7 + 0.3 * _ease((t - t0) / 0.12)
                e = emoji_rgba(ch, max(8, int(size * pop)))
                if e is not None:
                    y = int(self.oh * (0.25 if self.spec["captions"].get("position") == "top" else 0.52)) - e.shape[0] // 2
                    blend(img, e, self.ow // 2 - e.shape[1] // 2, y)

        if self.logo is not None:
            pos = self.logo_cfg.get("pos", "top-right")
            m = int(self.ow * 0.04)
            x = m if "left" in pos else self.ow - self.logo.shape[1] - m
            y = m + int(self.oh * 0.03) if "top" in pos else self.oh - self.logo.shape[0] - m - int(self.oh * 0.12)
            op = float(self.logo_cfg.get("opacity", 0.9))
            if self.logo_cfg.get("animate", True):
                op *= _ease(t / 0.6)
            blend(img, self.logo, x, y, op)

        if self.sharpen:
            blur = cv2.GaussianBlur(img, (0, 0), 1.2)
            img = cv2.addWeighted(img, 1.45, blur, -0.45, 0)
        if self.to_black and t > self.total - self.to_black:      # soft ending instead of a hard stop
            k = max(0.0, (self.total - t) / self.to_black)
            img = (img.astype(np.float32) * _ease(k)).astype(np.uint8)
        return img


def wrap_intro_outro(main_mp4, intro, outro, size, fps, out_mp4):
    """Concatenate optional intro/outro videos (scaled to fit) around the rendered clip."""
    files = [p for p in (intro, main_mp4, outro) if p]
    if len(files) == 1:
        return main_mp4
    w, h = size
    inputs, chains = [], []
    for i, p in enumerate(files):
        inputs += ["-i", str(p)]
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                      f"setsar=1,fps={fps},format=yuv420p[v{i}];[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    cat = "".join(f"[v{i}][a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=1:a=1[v][a]"
    subprocess.run([FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chains + [cat]), "-map", "[v]", "-map", "[a]",
                    "-c:v", "h264_videotoolbox", "-b:v", "12M", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_mp4)],
                   check=True)
    return out_mp4
