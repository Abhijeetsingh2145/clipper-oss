"""Render a clip spec to MP4: ffmpeg decode -> Python compositor (layout + effects) -> ffmpeg encode
with ASS captions and the mixed audio track."""
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from .audio import render_audio, synthesize
from .sfx import auto_events
from .sfx import library as sfx_library
from .captions import build_ass
from .common import FFMPEG, open_frames, read_exact
from .enhance import Effects, wrap_intro_outro
from .frames import Frame
from .layouts import Framer
from .rules import PROFANITY
from .enhance import zoom as zoom_img
from .spec import normalize, out_size, output_words, timeline_joins


def censor_set(spec):
    cap = spec["captions"]
    if not cap.get("censor"):
        return set()
    return {w.lower() for w in PROFANITY} | {w.lower().strip() for w in cap.get("censor_words", []) if w.strip()}


def render(spec, an, info, out_path, preview=False, extra_audio=None, progress=None):
    spec = normalize(spec)
    out_path = Path(out_path).resolve()
    work = out_path.parent
    stem = out_path.stem
    pieces, joins = timeline_joins(spec)
    if not pieces:
        raise ValueError("This clip has no footage left after cuts")
    total = max(o + e - s for s, e, o in pieces)
    join_type = (spec.get("join") or {}).get("type", "cut")
    ow, oh = out_size(spec, preview)
    fps = int(min(spec.get("fps", 30), round(info["fps"]), 24 if preview else 60))
    # decode at the smallest size that still fills the output when cropped at full source height
    work_h = min(info["height"], max(oh, 540)) // 2 * 2
    work_w = int(round(info["width"] * work_h / info["height"] / 2)) * 2

    words = output_words(spec)
    cens = censor_set(spec)
    frame = Frame(spec, ow, oh, total, preview)
    cw, ch = frame.content_size                       # the picture area inside the reel frame
    beeps = [(w["s"], w["e"]) for w in words if re.sub(r"[^\w']", "", w["w"]).lower() in cens]

    # captions
    ass_name = f"{stem}.ass"
    cap = dict(spec["captions"])
    if frame.caption_margin() is not None:
        cap["margin_v_px"] = frame.caption_margin()
    title = spec.get("title") if frame.style == "none" else None          # framed: the headline is the title
    (work / ass_name).write_text(build_ass(words, cap, total, (ow, oh), title, spec.get("watermark", ""), cens),
                                 encoding="utf-8")
    font_dir = spec["captions"].get("font_dir")
    ass_filter = f"ass={ass_name}" + (f":fontsdir='{font_dir}'" if font_dir and os.path.isdir(font_dir) else "")

    # audio: speech + censor beeps + SFX + music + AI voice-over
    wav = work / f"{stem}.wav"
    extra = dict(extra_audio or {})
    extra.setdefault("beeps", beeps)
    a = spec["audio"]
    if a.get("music") and Path(a["music"]).exists():
        extra["music"] = a["music"]
    if a.get("sfx"):
        lib = sfx_library()
        events = list(spec.get("sfx_events") or auto_events(spec, pieces))
        extra["sfx"] = [(e["t"], lib[e["name"]]) for e in events if e["name"] in lib and 0 <= e["t"] < total]
    vo = a.get("voiceover", {})
    if vo.get("text", "").strip():
        voice_wav = work / f"{stem}.voice.wav"
        synthesize(vo["text"], voice_wav, vo.get("voice") or "af_heart", vo.get("lang") or "en-us")
        extra["voice"] = voice_wav
    render_audio(spec, info, pieces, total, wav, extra, joins)

    if frame.style not in ("none", "cinema") and spec["layout"].get("mode") in ("auto", "fit"):
        spec = {**spec, "layout": {**spec["layout"], "mode": "auto", "fill": True}}   # picture fills its box
    framer = Framer(spec, an, work_w, work_h, cw, ch, pieces)
    # framed: the frame fades the whole canvas to black, so the picture must not fade twice
    fx_spec = spec if frame.style == "none" else {**spec, "ending": {**spec.get("ending", {}), "to_black": 0}}
    fx = Effects(fx_spec, words, total, cw, ch, fps, pieces)
    tmp = work / f"{stem}.main.mp4" if (spec.get("intro") or spec.get("outro")) else out_path
    enc = subprocess.Popen(
        [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}", "-r", str(fps), "-i", "-",
         "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-vf", f"{ass_filter},format=yuv420p",
         "-c:v", "h264_videotoolbox", "-b:v", "2500k" if preview else ("14M" if ow * oh <= 1080 * 1920 else "18M"),
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(tmp)],
        stdin=subprocess.PIPE, cwd=work)
    frame_bytes = work_w * work_h * 3
    total_frames = int(round(total * fps))
    done = 0
    held = []                                  # last frames of a section, kept back to blend into the next
    starts = [o for i, (_, _, o) in enumerate(pieces) if i in joins]     # output times where a new section begins

    def emit(img):
        nonlocal done
        enc.stdin.write(np.ascontiguousarray(img).tobytes())
        done += 1
        if progress and done % (fps * 5) == 0:
            progress(done / max(total_frames, 1))

    try:
        for i, (s, e, o) in enumerate(pieces):
            n = int(round((o + e - s) * fps)) - int(round(o * fps))
            blend = int(round(joins.get(i, 0) * fps)) if join_type == "dissolve" else 0
            if not blend:
                for img in held:
                    emit(img)
                held = []
            nxt_blend = int(round(joins.get(i + 1, 0) * fps)) if join_type == "dissolve" else 0
            dec, head = open_frames(spec["source"], f"fps={fps},scale={work_w}:{work_h}:flags=bicubic", s, e - s + 0.5)
            last = None
            for k in range(n):
                buf, head = read_exact(dec, frame_bytes, head)
                if len(buf) == frame_bytes:
                    last = np.frombuffer(buf, np.uint8).reshape(work_h, work_w, 3)
                if last is None:
                    last = np.zeros((work_h, work_w, 3), np.uint8)
                t_src, t_out = s + k / fps, o + k / fps
                img = fx.apply(np.ascontiguousarray(framer.compose(last, t_src)), t_out)
                since = min((t_out - b for b in starts if 0 <= t_out - b < 0.35), default=None)
                if join_type == "zoom" and since is not None:        # punch in on the new shot, settle in 0.3 s
                    img = zoom_img(img, 1 + 0.14 * (1 - min(1.0, since / 0.3)) ** 2)
                img = frame.place(img, t_out)
                if join_type == "flash" and since is not None and since < 0.15:
                    a = 0.85 * (1 - since / 0.15)
                    img = (img.astype(np.float32) * (1 - a) + 255 * a).astype(np.uint8)
                if k < blend and held:                               # crossfade: outgoing frame under incoming
                    a = (k + 1) / (blend + 1)
                    a = a * a * (3 - 2 * a)
                    img = (held.pop(0).astype(np.float32) * (1 - a) + img.astype(np.float32) * a).astype(np.uint8)
                    emit(img)
                elif nxt_blend and k >= n - nxt_blend:
                    held.append(img)                                 # held back for the next section's blend
                else:
                    emit(img)
            dec.kill()
            dec.wait()
        for img in held:
            emit(img)
    finally:
        enc.stdin.close()
    if enc.wait():
        raise RuntimeError(f"Encoding failed for {out_path.name}")
    wav.unlink(missing_ok=True)
    (work / f"{stem}.voice.wav").unlink(missing_ok=True)
    if tmp != out_path:
        intro, outro = spec.get("intro") or None, spec.get("outro") or None
        wrap_intro_outro(tmp, intro, outro, (ow, oh), fps, out_path)
        tmp.unlink(missing_ok=True)
    return out_path
