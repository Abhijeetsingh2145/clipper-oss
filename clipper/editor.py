"""AI Producer: plain-English edit requests -> validated spec operations, each logged.

The model never writes the spec directly. It proposes operations from a fixed menu; every
operation's arguments are checked and clamped here before anything changes.
"""
import copy
import re

from . import spec as S
from .captions import TEMPLATES
from .llm import ask_json

OPS = {
    "captions": "args: template (one of %s), color, highlight (hex or name), font, position (top/middle/lower/bottom), "
                "uppercase (bool), words_per_line (1-8), emoji (bool), keywords (bool), speaker_colors (bool), censor (bool), "
                "offset (seconds, -1..1), enabled (bool)" % ", ".join(TEMPLATES),
    "trim": "args: start_delta, end_delta (seconds; negative start_delta = start earlier, positive end_delta = end later)",
    "layout": "args: mode (auto/track/fit/center/split/gameplay/screen/manual), manual_x (0-1), target_x (0-1 face to follow)",
    "aspect": "args: aspect (9:16, 1:1, 4:5, 16:9)",
    "zoom": "args: mode ('auto' = zoom on key words, 'none' = remove zooms, 'at' = one zoom), t, dur, scale (1.05-1.4)",
    "audio": "args: enhance, denoise, remove_fillers, remove_pauses, remove_retakes, sfx (bools), music_volume (0-1), duck (bool)",
    "title": "args: text, show (bool)",
    "transitions": "args: style (punch/none)",
    "sharpen": "args: on (bool)",
    "delete_words": "args: phrase (exact words to cut from the clip)",
    "regenerate": "args: target (keywords/emoji/title/post/broll/hook)",
}
SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {"type": "array", "items": {"type": "object", "properties": {
            "op": {"type": "string", "enum": list(OPS)}, "args": {"type": "object"}}, "required": ["op", "args"]}},
        "explanation": {"type": "string"},
    },
    "required": ["ops", "explanation"],
}


def _b(v):
    return v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1", "on")


def _f(v, lo, hi, default):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def plan(command, spec, model=None):
    summary = {
        "duration": round(S.duration(spec), 1), "aspect": spec["aspect"], "layout": spec["layout"]["mode"],
        "captions": {k: spec["captions"].get(k) for k in ("template", "color", "highlight", "position", "uppercase", "emoji", "keywords")},
        "zooms": len(spec.get("zooms", [])), "transitions": spec.get("transitions"), "title": spec.get("title"),
        "audio": {k: spec["audio"].get(k) for k in ("enhance", "denoise", "remove_fillers", "remove_pauses", "sfx")},
        "text": " ".join(w["w"] for w in spec["words"] if not w.get("deleted"))[:1500],
    }
    menu = "\n".join(f"- {k}: {v}" for k, v in OPS.items())
    reply = ask_json(
        "You are a video editor's assistant. Turn the request into operations from this menu ONLY. "
        "Include ONLY the args the request explicitly asks for; never change anything else. "
        "If the request can't be done with the menu, return no ops and say why.\n"
        f"MENU:\n{menu}\n\nCURRENT CLIP: {summary}\n\nREQUEST: {command}",
        SCHEMA, model, temperature=0,
    )
    reply["ops"] = guard(reply.get("ops", []), command)
    return reply


# caption settings the model may only touch when the request talks about them
MENTIONS = {
    "position": ("top", "bottom", "middle", "lower", "position", "move", "higher", "up", "down", "place"),
    "template": tuple(TEMPLATES) + ("style", "template", "look", "karaoke", "boxed", "minimal", "neon", "subtitle", "pop", "one word"),
    "font": ("font", "typeface"),
    "uppercase": ("caps", "upper", "lowercase", "lower case", "capital"),
    "words_per_line": ("word", "line", "shorter", "longer"),
    "offset": ("sync", "offset", "early", "late", "timing", "delay"),
    "speaker_colors": ("speaker",), "censor": ("censor", "swear", "curse", "bleep", "profan"),
    "emoji": ("emoji",), "keywords": ("keyword", "highlight", "important"),
}


def guard(ops, command):
    """Drop caption arguments the request never mentioned (small models like to 'help')."""
    c = command.lower()
    for o in ops:
        if o.get("op") == "captions":
            o["args"] = {k: v for k, v in (o.get("args") or {}).items()
                         if k not in MENTIONS or any(m in c for m in MENTIONS[k])}
    return [o for o in ops if o.get("op") != "captions" or o["args"]]


def apply_ops(spec, ops, an=None, rules=None, model=None):
    """Apply validated operations. Returns (new_spec, list of human-readable changes)."""
    spec = copy.deepcopy(spec)
    done = []
    for o in ops:
        op, a = o.get("op"), o.get("args") or {}
        if op == "captions":
            cap = spec["captions"]
            if a.get("template") in TEMPLATES:
                cap["template"] = a["template"]
            for k in ("color", "highlight", "font"):
                if isinstance(a.get(k), str) and a[k].strip():
                    cap[k] = a[k].strip()[:40]
            if a.get("position") in ("top", "middle", "lower", "bottom"):
                cap["position"] = a["position"]
            for k in ("uppercase", "emoji", "keywords", "speaker_colors", "censor", "enabled"):
                if k in a:
                    cap[k] = _b(a[k])
            if "words_per_line" in a:
                cap["words_per_line"] = int(_f(a["words_per_line"], 1, 8, 3))
            if "offset" in a:
                cap["offset"] = _f(a["offset"], -1, 1, 0)
            if cap.get("emoji") and not any(w.get("emoji") for w in spec["words"]):
                regenerate(spec, "emoji", an, rules, model)
            done.append(f"Captions: {', '.join(f'{k}={v}' for k, v in a.items())}")
        elif op == "trim":
            seg_first, seg_last = spec["segments"][0], spec["segments"][-1]
            ds, de = _f(a.get("start_delta", 0), -60, 60, 0), _f(a.get("end_delta", 0), -60, 60, 0)
            seg_first["start"] = round(max(0.0, seg_first["start"] + ds), 3)
            seg_last["end"] = round(max(seg_last["start"] + 1, seg_last["end"] + de), 3)
            if an:
                _refresh_words(spec, an)
            done.append(f"Trimmed: start {ds:+.1f}s, end {de:+.1f}s")
        elif op == "layout":
            if a.get("mode") in S.LAYOUTS:
                spec["layout"]["mode"] = a["mode"]
            if "manual_x" in a:
                spec["layout"]["manual_x"] = _f(a["manual_x"], 0, 1, 0.5)
            if "target_x" in a:
                spec["layout"]["target_x"] = _f(a["target_x"], 0, 1, 0.5)
            done.append(f"Layout: {spec['layout']['mode']}")
        elif op == "aspect" and a.get("aspect") in S.ASPECTS:
            spec["aspect"] = a["aspect"]
            done.append(f"Aspect ratio: {a['aspect']}")
        elif op == "zoom":
            mode = a.get("mode", "auto")
            if mode == "none":
                spec["zooms"] = []
                done.append("Removed zooms")
            elif mode == "at":
                spec.setdefault("zooms", []).append({"t": _f(a.get("t"), 0, 3600, 0), "dur": _f(a.get("dur"), 0.4, 5, 1.5),
                                                     "scale": _f(a.get("scale"), 1.05, 1.4, 1.15)})
                done.append(f"Zoom at {spec['zooms'][-1]['t']:.1f}s")
            else:
                from .enrich import auto_zooms
                if not any(w.get("kw") for w in spec["words"]):
                    regenerate(spec, "keywords", an, rules, model)
                auto_zooms(spec, S.output_words(spec), scale=_f(a.get("scale"), 1.05, 1.4, 1.12))
                done.append(f"Added {len(spec['zooms'])} zooms on key words")
        elif op == "audio":
            au = spec["audio"]
            for k in ("enhance", "denoise", "remove_fillers", "remove_pauses", "remove_retakes", "sfx", "duck"):
                if k in a:
                    au[k] = _b(a[k])
            if "music_volume" in a:
                au["music_volume"] = _f(a["music_volume"], 0, 1, 0.12)
            recompute_cuts(spec)
            done.append(f"Audio: {', '.join(f'{k}={v}' for k, v in a.items())}")
        elif op == "title":
            if isinstance(a.get("text"), str) and a["text"].strip():
                spec["title"]["text"] = a["text"].strip()[:80]
            if "show" in a:
                spec["title"]["show"] = _b(a["show"])
            done.append(f"Title: {spec['title']['text']!r} ({'shown' if spec['title']['show'] else 'hidden'})")
        elif op == "transitions":
            spec["transitions"] = "punch" if a.get("style") == "punch" else "none"
            done.append(f"Transitions: {spec['transitions']}")
        elif op == "sharpen":
            spec["sharpen"] = _b(a.get("on", True))
            done.append(f"Sharpen: {'on' if spec['sharpen'] else 'off'}")
        elif op == "delete_words" and a.get("phrase"):
            n = delete_phrase(spec, str(a["phrase"]))
            done.append(f"Cut {n} word(s): {a['phrase']!r}" if n else f"Phrase not found: {a['phrase']!r}")
        elif op == "regenerate" and a.get("target"):
            done.append(regenerate(spec, a["target"], an, rules, model))
    return spec, done


def _refresh_words(spec, an):
    """After trimming/adding footage, pull in transcript words for the new ranges (keeping edits)."""
    old = {(round(w["s"], 2), round(w["e"], 2)): w for w in spec["words"]}
    words = []
    for seg in spec["segments"]:
        for w in an.words_between(seg["start"], seg["end"]):
            key = (round(w["s"], 2), round(w["e"], 2))
            words.append(old.get(key) or {"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3), "deleted": False})
    spec["words"] = words
    recompute_cuts(spec)


def recompute_cuts(spec):
    """Filler / retake / pause cuts from the clip's settings. Pauses are found in the audio itself
    (silence.py); the transcript-gap method is the fallback when the audio can't be calibrated."""
    from .enrich import filler_cuts, pause_cuts, retake_cuts
    from .silence import silence_cuts
    a, cuts = spec["audio"], []
    if spec.get("meta", {}).get("picked_by") == "montage":   # an edit of shots: its silences are deliberate
        spec["cuts"] = []
        return
    if a.get("remove_fillers"):
        cuts += filler_cuts(spec["words"])
    if a.get("remove_retakes"):
        cuts += retake_cuts(spec["words"])
    if a.get("remove_pauses"):
        keep = float(a.get("pause_max", 0.35))
        for seg in spec["segments"]:
            ws = [w for w in spec["words"] if seg["start"] <= w["s"] < seg["end"]]
            found = silence_cuts(spec["source"], seg["start"], seg["end"], ws, keep) if spec.get("source") else None
            cuts += found if found is not None else pause_cuts(ws, seg["start"], seg["end"], keep)
    spec["cuts"] = sorted(cuts)


def delete_phrase(spec, phrase):
    norm = lambda t: re.sub(r"[^\w']", "", t.lower())
    target = [norm(t) for t in phrase.split() if norm(t)]
    ws = [w for w in spec["words"] if not w.get("deleted")]
    toks = [norm(w["w"]) for w in ws]
    for i in range(len(toks) - len(target) + 1):
        if toks[i:i + len(target)] == target:
            for w in ws[i:i + len(target)]:
                w["deleted"] = True
            return len(target)
    return 0


def regenerate(spec, target, an=None, rules=None, model=None):
    """Redo one AI-made part without touching the rest of the edit."""
    from .enrich import enrich_words
    rules = rules or {}
    if target == "keywords":
        enrich_words(spec, model, keywords=True, emoji=False)
        return f"New keywords: {', '.join(w['w'] for w in spec['words'] if w.get('kw'))[:120]}"
    if target == "emoji":
        keep_kw = {id(w): w.get("kw") for w in spec["words"]}
        enrich_words(spec, model, keywords=False, emoji=True)
        for w in spec["words"]:
            w["kw"] = keep_kw.get(id(w), w.get("kw"))
        spec["captions"]["emoji"] = True
        return f"New emoji: {' '.join(w['emoji'] for w in spec['words'] if w.get('emoji'))}"
    if target in ("title", "post"):
        from .postcopy import write_post
        spoken = " ".join(w["w"] for w in S.output_words(spec))
        post = write_post(spoken, spec["meta"].get("hook_idea", ""), rules, rules.get("_text", ""), model) if rules else None
        if target == "title" and rules is not None:
            from .hooktext import write_hook
            hook = write_hook(spoken, spec["meta"].get("mode"), rules, model, spec["captions"].get("script") == "roman",
                              (post or {}).get("title", ""))
            if hook:
                spec["title"].update(text=hook, show=True, seconds=0)
                return f"New on-screen hook: {hook!r}"
        if post:
            spec["meta"]["post"] = post
            return "New post caption"
        return "Couldn't regenerate the post (no campaign rules loaded)"
    if target == "broll":
        from .broll import fill
        inserts, notes = fill(spec, S.output_words(spec), S.duration(spec), model)
        return f"B-roll: {len(inserts)} insert(s)" + (f" · {'; '.join(notes)}" if notes else "")
    if target == "hook" and an:
        return better_hook(spec, an)
    return f"Unknown target: {target}"


def better_hook(spec, an, window=12.0):
    """Try sentence starts up to `window` s around the current start; keep the one whose opening
    scores best on the measured hook checks (speech timing, loudness, question/number/'you')."""
    from .scoring import score_clip, video_stats
    seg = spec["segments"][0]
    end = seg["end"]
    tw = an.tr["words"]
    starts = sorted({w["s"] for i, w in enumerate(tw) if abs(w["s"] - seg["start"]) <= window and w["s"] < end - 5
                     and (i == 0 or tw[i - 1]["w"][-1:] in ".?!")})
    if not starts:
        return "No other sentence starts nearby"
    stats = video_stats(an)
    rules = {"min_seconds": 0, "max_seconds": 1e9, "banned_words": []}
    best = max(starts, key=lambda s: score_clip(max(0, s - 0.15), end, an, rules, use_llm=False, stats=stats)["parts"]["hook"]["score"])
    old = seg["start"]
    seg["start"] = round(max(0.0, best - 0.15), 3)
    _refresh_words(spec, an)
    return f"Hook: start moved {seg['start'] - old:+.1f}s to the strongest nearby opening"
