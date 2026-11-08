"""Reel frames: the video sits inside a designed canvas with a fixed headline, the formats that
dominate Instagram Reels / TikTok right now. A headline that stays on screen tells a scroller what
they are watching in the first second (most watch muted), which is what keeps them past second 3.

Styles (spec["frame"]["style"]):
- headline: solid bar on top with a bold headline, the video fills the rest (the "meme/news" frame).
- bars:     headline bar on top AND a black caption bar at the bottom, the video in between.
- cinema:   black canvas, the video as a full-width band (16:9 is shown uncropped), headline above,
            captions below the picture.
- card:     the video in a rounded 4:5 card floating over a blurred copy of itself.
- square:   a rounded 1:1 video on a colour gradient, headline above, captions below.
- neon:     the video in a glowing accent-coloured frame on black.
- post:     a social-post card: your name + @handle, the headline as the post text, the video below.
            Generic (no platform logos), filled from Settings > Creator name / handle.
- polaroid: the video as a slightly tilted instant photo, the headline handwritten underneath.
- none:     plain full-screen video.
Any style can add a progress bar along the bottom (spec["frame"]["progress"]).

The headline is the clip's title (written from what is actually said in the clip) unless set by
hand, so the on-screen text stays true to the content, as campaign briefs require. One key word is
shown in the accent colour: the word(s) you wrap in *asterisks*, else the longest meaningful word.
"""
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SYS = "/System/Library/Fonts/"
BOLD = [SYS + "Supplemental/Arial Black.ttf", SYS + "Supplemental/Arial Bold.ttf"]
SEMI = [SYS + "Supplemental/Arial Bold.ttf", SYS + "Supplemental/Arial Black.ttf"]
REGULAR = [SYS + "Supplemental/Arial.ttf", SYS + "Supplemental/Arial Bold.ttf"]
HAND = [SYS + "Noteworthy.ttc", SYS + "MarkerFelt.ttc", SYS + "Supplemental/Bradley Hand Bold.ttf"]
STYLES = {"none": "Full screen", "headline": "Headline bar", "bars": "Top + bottom bars", "cinema": "Cinema band",
          "card": "Floating card", "square": "Square on gradient", "neon": "Neon frame", "post": "Social post",
          "polaroid": "Polaroid"}
STOP = {"the", "and", "that", "this", "with", "from", "your", "have", "what", "when", "they", "them", "just",
        "goes", "into", "about", "will", "would", "there", "their", "been", "were", "than", "then"}


def _hex(c, default=(255, 255, 255)):
    c = (c or "").lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else default


@lru_cache(maxsize=32)
def _font(path, size, kind="bold"):
    cands = {"bold": BOLD, "semi": SEMI, "regular": REGULAR, "hand": HAND}[kind]
    for p in ([path] if path and kind == "bold" else []) + cands:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _tokens(text, upper):
    """Words with a highlight flag: *marked* words, else the longest meaningful word."""
    words = text.split()
    marked = [w.startswith("*") or w.endswith("*") for w in words]
    clean = [w.strip("*") for w in words]
    if not any(marked) and len(clean) >= 3:
        cand = [(len(re.sub(r"\W", "", w)), i) for i, w in enumerate(clean) if re.sub(r"\W", "", w).lower() not in STOP]
        if cand:
            marked[max(cand)[1]] = True
    return [((w.upper() if upper else w), m) for w, m in zip(clean, marked)]


def _layout_words(tokens, font, max_w, draw):
    lines, cur = [], []
    space = draw.textlength(" ", font=font)
    for w, m in tokens:
        width = sum(draw.textlength(x, font=font) for x, _ in cur) + space * len(cur) + draw.textlength(w, font=font)
        if cur and width > max_w:
            lines.append(cur)
            cur = []
        cur.append((w, m))
    if cur:
        lines.append(cur)
    return lines


def sentence_case(text):
    """'Last Of My Bloodline Speech' -> 'Last of my bloodline speech' (titles read like posts)."""
    words = text.split()
    if len(words) > 2 and sum(w[:1].isupper() for w in words) >= len(words) - 1:
        return " ".join([words[0]] + [w if w.isupper() and len(w) > 1 else w[:1].lower() + w[1:] for w in words[1:]])
    return text


def draw_headline(canvas, text, box, color, accent, upper=True, max_size=84, kind="bold", align="center",
                  font_path="", max_lines=3, highlight=True, marker=False):
    """Fit the headline into box (x, y, w, h) at the largest size that fits, one word in the accent.
    marker=True (light backgrounds, where yellow text would vanish): the word gets an accent
    highlighter box behind it instead and keeps the dark text colour."""
    if not text:
        return
    x, y, w, h = box
    toks = _tokens(text, upper) if highlight else [((t.upper() if upper else t).strip("*"), False) for t in text.split()]
    d = ImageDraw.Draw(canvas)
    for size in range(int(max_size), 23, -3):
        f = _font(font_path, size, kind)
        lines = _layout_words(toks, f, w, d)
        lh = int(size * 1.17)
        if len(lines) <= max_lines and lh * len(lines) <= h:
            break
    space = d.textlength(" ", font=f)
    top = y + (h - lh * len(lines)) // 2
    for i, line in enumerate(lines):
        lw = sum(d.textlength(t, font=f) for t, _ in line) + space * (len(line) - 1)
        cx = x if align == "left" else x + (w - lw) / 2
        for t, m in line:
            tw = d.textlength(t, font=f)
            if m and marker:
                pad = int(f.size * 0.12)
                d.rounded_rectangle((cx - pad, top + i * lh - pad * 0.3, cx + tw + pad, top + i * lh + f.size * 1.08),
                                    radius=pad, fill=accent)
            d.text((cx, top + i * lh), t, font=f, fill=color if (marker or not m) else accent)
            cx += tw + space


def _rounded(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return m


def _gradient(w, h, top, bottom):
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    g = np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t
    return Image.fromarray(np.repeat(g, w, axis=1).astype(np.uint8))


def _bgr(pil_rgb):
    return np.asarray(pil_rgb.convert("RGB"))[..., ::-1].copy()


class Frame:
    def __init__(self, spec, ow, oh, total, preview=False):
        f = dict(spec.get("frame") or {})
        self.style = f.get("style") if f.get("style") in STYLES else "none"
        self.progress = bool(f.get("progress"))
        self.ow, self.oh, self.total = ow, oh, max(total, 0.1)
        self.to_black = min(float((spec.get("ending") or {}).get("to_black") or 0), total / 3)
        cap = spec.get("captions", {})
        self.accent = _hex(f.get("accent") or cap.get("highlight") or "#FFD400", (255, 212, 0))
        self.bar_color = _hex(f.get("progress_color"), self.accent)
        text = "" if f.get("no_text") else (f.get("text") or (spec.get("title") or {}).get("text") or "").strip()
        self.k = k = ow / 1080
        self.font_path, self.upper = f.get("font") or "", f.get("uppercase", True)
        self.box, self.mask, self.angle = (0, 0, ow, oh), None, 0.0
        self.base = self.over = None                 # BGR background / RGBA layer above the picture
        self.dynamic_bg = False                      # card + polaroid: blurred copy of the picture behind
        self.cap_margin = None
        self._bg_for_text = (0, 0, 0)                 # styles on light backgrounds override this
        if self.style != "none":
            getattr(self, "_" + self.style)(f, text, k)

    # ---------- styles ----------
    @staticmethod
    def _light(rgb):
        return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255 > 0.6

    def _head(self, img, text, box, color, size, **kw):
        kw.setdefault("marker", self._light(color) is False and self._light(self._bg_for_text))
        draw_headline(img, text, box, color, self.accent, kw.pop("upper", self.upper), int(size * self.k),
                      font_path=self.font_path, **kw)

    def _headline(self, f, text, k):
        ow, oh = self.ow, self.oh
        bar = int(oh * 0.24) // 2 * 2 if text else 0
        self.box = (0, bar, ow, oh - bar)
        img = Image.new("RGB", (ow, oh), _hex(f.get("bg"), (255, 255, 255)))
        self._bg_for_text = _hex(f.get("bg"), (255, 255, 255))
        self._head(img, text, (int(60 * k), int(40 * k), ow - int(120 * k), bar - int(70 * k)), _hex(f.get("fg"), (0, 0, 0)), 84)
        self.base = _bgr(img)

    def _bars(self, f, text, k):
        ow, oh = self.ow, self.oh
        top = int(oh * 0.20) // 2 * 2 if text else 0
        bottom = int(oh * 0.19) // 2 * 2
        self.box = (0, top, ow, oh - top - bottom)
        img = Image.new("RGB", (ow, oh), (0, 0, 0))
        ImageDraw.Draw(img).rectangle((0, 0, ow, top), fill=_hex(f.get("bg"), (255, 255, 255)))
        self._bg_for_text = _hex(f.get("bg"), (255, 255, 255))
        self._head(img, text, (int(60 * k), int(30 * k), ow - int(120 * k), top - int(55 * k)), _hex(f.get("fg"), (0, 0, 0)), 80)
        self.base = _bgr(img)
        self.cap_margin = int(bottom * 0.30)                                  # captions in the bottom bar

    def _cinema(self, f, text, k):
        ow, oh = self.ow, self.oh
        band, top = int(ow * 9 / 16) // 2 * 2, int(oh * 0.30) // 2 * 2
        self.box = (0, top, ow, band)
        img = Image.new("RGB", (ow, oh), _hex(f.get("bg"), (0, 0, 0)))
        self._head(img, text, (int(60 * k), int(oh * 0.07), ow - int(120 * k), top - int(oh * 0.09)), _hex(f.get("fg"), (255, 255, 255)), 86)
        self.base = _bgr(img)
        self.cap_margin = max(0, oh - (top + band) - int(oh * 0.13))

    def _card(self, f, text, k):
        ow, oh = self.ow, self.oh
        cw = int(ow * 0.86) // 2 * 2
        ch, top = int(cw * 5 / 4) // 2 * 2, int(oh * 0.21) // 2 * 2
        self.box = ((ow - cw) // 2, top, cw, ch)
        self.mask = _rounded(cw, ch, int(46 * k))
        self.dynamic_bg = True
        layer = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
        self._head(layer, text, (int(60 * k), int(oh * 0.045), ow - int(120 * k), top - int(oh * 0.07)), _hex(f.get("fg"), (255, 255, 255)), 80)
        self.over = self._with_shadow(layer)
        self.cap_margin = max(0, oh - (top + ch) - int(oh * 0.085))

    def _square(self, f, text, k):
        ow, oh = self.ow, self.oh
        s = int(ow * 0.90) // 2 * 2
        top = int(oh * 0.25) // 2 * 2
        self.box = ((ow - s) // 2, top, s, s)
        self.mask = _rounded(s, s, int(36 * k))
        a = np.array(self.accent, np.float32)
        img = _gradient(ow, oh, _hex(f.get("bg"), tuple(int(v) for v in a * 0.45)), _hex(f.get("bg2"), (8, 8, 12)))
        self._head(img, text, (int(60 * k), int(oh * 0.05), ow - int(120 * k), top - int(oh * 0.075)), _hex(f.get("fg"), (255, 255, 255)), 82)
        self.base = _bgr(img)
        self.cap_margin = max(0, oh - (top + s) - int(oh * 0.12))

    def _neon(self, f, text, k):
        ow, oh = self.ow, self.oh
        cw = int(ow * 0.84) // 2 * 2
        ch, top = int(cw * 5 / 4) // 2 * 2, int(oh * 0.215) // 2 * 2
        x = (ow - cw) // 2
        self.box = (x, top, cw, ch)
        r = int(40 * k)
        self.mask = _rounded(cw, ch, r)
        glow = Image.new("RGB", (ow, oh), (0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((x - 6, top - 6, x + cw + 6, top + ch + 6), radius=r + 6, outline=self.accent, width=int(14 * k))
        g = cv2.GaussianBlur(np.asarray(glow, np.float32), (0, 0), 28 * k) * 2.2 + cv2.GaussianBlur(np.asarray(glow, np.float32), (0, 0), 8 * k)
        img = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
        self._head(img, text, (int(60 * k), int(oh * 0.05), ow - int(120 * k), top - int(oh * 0.08)), _hex(f.get("fg"), (255, 255, 255)), 80)
        self.base = _bgr(img)
        border = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle((x - 2, top - 2, x + cw + 2, top + ch + 2), radius=r + 2,
                                                 outline=self.accent + (255,), width=max(3, int(6 * k)))
        self.over = self._layer(border)
        self.cap_margin = max(0, oh - (top + ch) - int(oh * 0.09))

    def _post(self, f, text, k):
        from . import settings as cfg
        conf = cfg.load()
        name = (f.get("name") or conf.get("creator_name") or "").strip()
        handle = (f.get("handle") or conf.get("creator_handle") or "").strip().lstrip("@")
        dark = f.get("theme") == "dark"
        ow, oh = self.ow, self.oh
        bg, fg, sub = ((0, 0, 0), (255, 255, 255), (113, 118, 123)) if dark else ((255, 255, 255), (15, 20, 25), (83, 100, 113))
        img = Image.new("RGB", (ow, oh), _hex(f.get("bg"), bg))
        d = ImageDraw.Draw(img)
        pad, y = int(56 * k), int(oh * 0.075)
        if name or handle:
            av = int(104 * k)
            d.ellipse((pad, y, pad + av, y + av), fill=self.accent)
            initial = (name or handle)[:1].upper()
            fi = _font("", int(54 * k), "bold")
            d.text((pad + av / 2, y + av / 2), initial, font=fi, fill=(255, 255, 255), anchor="mm")
            tx = pad + av + int(26 * k)
            if name:
                d.text((tx, y + av * 0.12), name, font=_font("", int(40 * k), "semi"), fill=fg)
            if handle:
                d.text((tx, y + av * (0.56 if name else 0.3)), "@" + handle, font=_font("", int(34 * k), "regular"), fill=sub)
            y += av + int(30 * k)
        text_h = int(oh * 0.15) if text else 0
        draw_headline(img, sentence_case(text), (pad, y, ow - 2 * pad, text_h), fg, self.accent, upper=False, max_size=int(58 * k),
                      kind="semi", align="left", max_lines=3, marker=not dark)
        y += text_h + int(28 * k)
        vw = ow - 2 * int(40 * k)
        vh = min(int(vw * 5 / 4), oh - y - int(oh * 0.06)) // 2 * 2
        self.box = (int(40 * k), y // 2 * 2, vw, vh)
        self.mask = _rounded(vw, vh, int(28 * k))
        self.base = _bgr(img)

    def _polaroid(self, f, text, k):
        ow, oh = self.ow, self.oh
        pw = int(ow * 0.80) // 2 * 2
        m = int(pw * 0.055)
        pic = pw - 2 * m
        ph = pic + m + int(pw * 0.25)
        px, py = (ow - pw) // 2, int(oh * 0.13)
        self.angle = float(f.get("tilt", -3.0))
        self.box = (px + m, py + m, pic, pic)
        self.rot = cv2.getRotationMatrix2D((px + pw / 2, py + ph / 2), self.angle, 1.0)
        self.dynamic_bg = True
        layer = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rectangle((px, py, px + pw, py + ph), fill=(250, 248, 242, 255))
        draw_headline(layer, sentence_case(text), (px + m, py + m + pic + int(pw * 0.02), pic, int(pw * 0.20)), (35, 35, 40),
                      self.accent, upper=False, max_size=int(64 * k), kind="hand", max_lines=2, marker=True)
        arr = np.asarray(layer)
        warped = cv2.warpAffine(arr, self.rot, (ow, oh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
        shadow = cv2.GaussianBlur(warped[..., 3].astype(np.float32), (0, 0), 22 * k)
        self.over_shadow = (np.clip(shadow / 255.0 * 0.8, 0, 1))[..., None]
        self.over = (warped[..., :3][..., ::-1].astype(np.float32), (warped[..., 3:] / 255.0).astype(np.float32))
        self.over_below = True                                                 # the print sits under the photo
        self.cap_margin = max(0, oh - int(py + ph + oh * 0.05) - int(oh * 0.09))

    def _layer(self, rgba):
        a = np.asarray(rgba)
        return a[..., :3][..., ::-1].astype(np.float32), (a[..., 3:] / 255.0).astype(np.float32)

    def _with_shadow(self, rgba):
        rgb, a = self._layer(rgba)
        self.over_shadow = (np.clip(cv2.GaussianBlur(a[..., 0], (0, 0), 6 * self.k) * 1.4, 0, 1))[..., None] * 0.6
        return rgb, a

    # ---------- per frame ----------
    @property
    def content_size(self):
        return self.box[2], self.box[3]

    def caption_margin(self):
        """Bottom margin (px) that keeps captions clear of the frame design, or None for the default."""
        return self.cap_margin

    def _fade(self, t):
        if self.to_black and t > self.total - self.to_black:
            return max(0.0, (self.total - t) / self.to_black)
        return 1.0

    def _blur_bg(self, content):
        small = cv2.resize(content, (max(1, self.ow // 8), max(1, self.oh // 8)), interpolation=cv2.INTER_AREA)
        return cv2.resize(cv2.GaussianBlur(small, (0, 0), 3), (self.ow, self.oh), interpolation=cv2.INTER_LINEAR).astype(np.float32) * 0.55

    def _over(self, img):
        if self.over is None:
            return img
        rgb, a = self.over
        if getattr(self, "over_shadow", None) is not None:
            img = img * (1 - self.over_shadow)
        return img * (1 - a) + rgb * a

    def place(self, content, t):
        """content: BGR image of content_size (layout + effects applied). Returns the full canvas."""
        if self.style == "none":
            img = content
        else:
            x, y, w, h = self.box
            img = self._blur_bg(content) if self.dynamic_bg else self.base.astype(np.float32)
            if getattr(self, "over_below", False):
                img = self._over(img)
            if self.angle:                                                     # tilted photo
                M = self.rot.copy()
                M[:, 2] += M[:, :2] @ np.array([x, y], np.float64)
                pic = cv2.warpAffine(content, M, (self.ow, self.oh), flags=cv2.INTER_LINEAR)
                a = cv2.warpAffine(np.ones((h, w), np.float32), M, (self.ow, self.oh), flags=cv2.INTER_LINEAR)[..., None]
                img = img * (1 - a) + pic.astype(np.float32) * a
            elif self.mask is not None:
                mk = np.asarray(self.mask, np.float32)[..., None] / 255.0
                img[y:y + h, x:x + w] = img[y:y + h, x:x + w] * (1 - mk) + content.astype(np.float32) * mk
            else:
                img[y:y + h, x:x + w] = content
            if not getattr(self, "over_below", False):
                img = self._over(img)
            k = self._fade(t)
            if k < 1:
                img = img * k
            img = np.clip(img, 0, 255).astype(np.uint8)
        if self.progress:
            bh = max(6, self.oh // 160)
            fill = int(self.ow * min(1.0, t / self.total))
            img[self.oh - bh:, :] = (img[self.oh - bh:, :].astype(np.float32) * 0.45).astype(np.uint8)
            img[self.oh - bh:, :fill] = self.accent[::-1] if self.bar_color is None else self.bar_color[::-1]
        return img
