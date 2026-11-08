"""Editable clip spec: everything needed to (re)render one clip, plus its version history.

Times in `segments`, `words`, `cuts` are SOURCE-video seconds. The renderer maps them onto the
output timeline with `timeline()`, so trimming, reordering, deleting words or removing pauses
never loses caption sync.
"""
import copy
import json
import time
from pathlib import Path

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
LAYOUTS = ("auto", "track", "fit", "center", "split", "gameplay", "screen", "manual")

DEFAULT = {
    "version": 2,
    "source": "",
    "segments": [],                  # [{"start", "end"}] in play order
    "cuts": [],                      # [[start, end]] removed ranges (fillers, pauses, retakes)
    "aspect": "9:16",
    "fps": 30,
    "layout": {"mode": "auto", "manual_x": 0.5, "target_track": None, "facecam": None, "switching": True},
    "words": [],                     # [{"w","s","e","deleted","kw","emoji","spk"}]
    "captions": {
        "enabled": True, "template": "bold_pop", "font": "", "color": "", "highlight": "",
        "uppercase": None, "position": "", "words_per_line": 0, "offset": 0.0,
        "emoji": False, "keywords": True, "speaker_colors": False,
        "censor": False, "censor_words": [], "font_dir": "",
    },
    "title": {"text": "", "show": False, "seconds": 4.0},
    "watermark": "",
    "logo": {"path": "", "pos": "top-right", "scale": 0.16, "opacity": 0.9, "animate": True},
    "overlays": [],                  # [{"type": "image"|"text", "path"/"text", "t", "dur", "x", "y", "scale"}] output time
    "zooms": [],                     # [{"t", "dur", "scale"}] output time
    "transitions": "none",           # none | punch (zoom punch at jump cuts)
    "broll": [],                     # [{"t", "dur", "path", "kind": "video"|"image", "source"}] output time
    "audio": {
        "enhance": False, "denoise": False, "loudnorm": True,
        "remove_fillers": False, "remove_pauses": False, "pause_max": 0.35, "remove_retakes": False,
        "music": "", "music_volume": 0.12, "duck": True,
        "sfx": False, "sfx_volume": 0.5,
        "voiceover": {"text": "", "voice": "", "lang": "en-us", "mix": "replace"},
    },
    "ending": {"fade": 0.35, "to_black": 0.0},
    "frame": {"style": "none", "text": "", "progress": False},
    "join": {"type": "cut", "dur": 0.2},     # how sections meet: cut / dissolve (crossfade) / flash / zoom   # reel frame (frames.py): headline/cinema/card   # seconds of audio fade-out / video fade-to-black at the end
    "intro": "", "outro": "",
    "sharpen": False,
    "meta": {},                      # title, post text, score breakdown, picked_by, reason...
}


def new_spec(**fields):
    spec = copy.deepcopy(DEFAULT)
    for k, v in fields.items():
        if isinstance(v, dict) and isinstance(spec.get(k), dict):
            spec[k].update(v)
        else:
            spec[k] = v
    return spec


def normalize(spec):
    """Fill fields added in later versions so old specs keep working."""
    out = new_spec()
    spec = {k: v for k, v in spec.items() if k != "audio_bed"}   # retired: montage shots now keep their own sound
    for k, v in spec.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = copy.deepcopy(out[k])
            merged.update(v)
            if k == "audio":
                merged["voiceover"] = {**out["audio"]["voiceover"], **v.get("voiceover", {})}
            out[k] = merged
        else:
            out[k] = v
    return out


def out_size(spec, preview=False):
    if spec["aspect"] == "original" and spec.get("src_size"):
        sw, sh = spec["src_size"]
        k = min(1.0, 1920 / max(sw, sh))
        w, h = int(sw * k) // 2 * 2, int(sh * k) // 2 * 2
    else:
        w, h = ASPECTS.get(spec["aspect"], ASPECTS["9:16"])
    return (w // 2 // 2 * 2, h // 2 // 2 * 2) if preview else (w, h)


def _subtract(start, end, holes):
    pieces, cur = [], start
    for hs, he in sorted(holes):
        if he <= cur or hs >= end:
            continue
        if hs > cur:
            pieces.append((cur, hs))
        cur = max(cur, he)
    if cur < end:
        pieces.append((cur, end))
    return [(s, e) for s, e in pieces if e - s >= 0.05]


JOIN_TYPES = ("cut", "dissolve", "flash", "zoom")


def join_overlap(spec):
    """Seconds two sections overlap at a join (only a dissolve blends them; the rest are instant)."""
    j = spec.get("join") or {}
    return float(j.get("dur", 0.2)) if j.get("type") == "dissolve" else 0.0


def timeline_joins(spec):
    """(pieces, joins): pieces [(src_start, src_end, out_start)] after removing cuts and deleted
    words; joins {piece_index: overlap_s} for pieces that start a new section. With a dissolve the
    incoming section starts `overlap` seconds before the outgoing one ends, so everything placed on
    the output timeline (captions, zooms, audio) stays in sync with the blend."""
    holes = [tuple(c) for c in spec["cuts"]] + [(w["s"], w["e"]) for w in spec["words"] if w.get("deleted")]
    d = join_overlap(spec)
    out, joins, t = [], {}, 0.0
    for si, seg in enumerate(spec["segments"]):
        for pi, (s, e) in enumerate(_subtract(seg["start"], seg["end"], holes)):
            if si and pi == 0 and out:
                ov = min(d, (e - s) / 3, (out[-1][1] - out[-1][0]) / 3)
                joins[len(out)] = ov
                t -= ov
            out.append((s, e, t))
            t += e - s
    return out, joins


def timeline(spec):
    """[(src_start, src_end, out_start)] after removing cuts and deleted words."""
    return timeline_joins(spec)[0]


def duration(spec):
    pcs = timeline(spec)
    return max((o + e - s for s, e, o in pcs), default=0.0)


def src_to_out(pieces, t):
    for s, e, o in pieces:
        if s <= t <= e:
            return o + (t - s)
    return None


FILLER_WORDS = {"um", "uh", "erm", "er", "ah", "hmm", "mm", "uhm", "umm", "uhh"}


def output_words(spec, hide_fillers=True):
    """Caption words on the output timeline (dropped if their audio was cut). Fillers are kept in
    the transcript (so they can be cut) but hidden from captions."""
    pieces = timeline(spec)
    out = []
    for w in spec["words"]:
        if w.get("deleted"):
            continue
        if hide_fillers and "".join(ch for ch in w["w"].lower() if ch.isalpha()) in FILLER_WORDS:
            continue
        s, e = src_to_out(pieces, w["s"]), src_to_out(pieces, w["e"])
        if s is None or e is None or e < s:
            continue
        out.append({**w, "s": s, "e": e})
    return out


# ---------- persistence + history ----------

def load(path):
    return normalize(json.loads(Path(path).read_text()))


def save(path, spec, action="", by="user"):
    """Write the spec and keep every previous version for undo/restore, with an edit log."""
    path = Path(path)
    vdir = path.with_suffix("").parent / (path.stem + ".versions")
    vdir.mkdir(exist_ok=True)
    versions = sorted(vdir.glob("v*.json"), key=lambda p: int(p.stem[1:]))
    n = int(versions[-1].stem[1:]) + 1 if versions else 1
    spec.setdefault("log", []).append({"v": n, "time": time.time(), "by": by, "action": action or "save"})
    spec["v"] = n
    data = json.dumps(spec, indent=1, ensure_ascii=False)
    (vdir / f"v{n}.json").write_text(data)
    path.write_text(data)
    return n


def versions(path):
    path = Path(path)
    vdir = path.parent / (path.stem + ".versions")
    out = []
    for p in sorted(vdir.glob("v*.json"), key=lambda p: int(p.stem[1:])):
        d = json.loads(p.read_text())
        last = (d.get("log") or [{}])[-1]
        out.append({"v": int(p.stem[1:]), "action": last.get("action", ""), "by": last.get("by", ""), "time": last.get("time")})
    return out


def restore(path, v):
    path = Path(path)
    src = path.parent / (path.stem + ".versions") / f"v{int(v)}.json"
    spec = normalize(json.loads(src.read_text()))
    save(path, spec, f"restored version {v}", "user")
    return spec
