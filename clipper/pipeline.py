"""End-to-end job: brief -> transcript -> analysis -> pick clips -> specs -> render -> report."""
import hashlib
import json
import re
from pathlib import Path

from . import settings as cfg
from . import spec as S
from .analysis import analyze
from .common import log, probe
from .compose import render
from .picker import pick_clips
from .postcopy import post_text, write_post
from .rules import DEFAULTS, load_rules
from .transcribe import DEFAULT_WHISPER, transcribe

LAYOUT_ALIASES = {"face": "track", "auto": "auto"}
CACHE = Path(__file__).resolve().parent.parent / "cache" / "videos"


def video_cache(video):
    """Transcript + analysis live per source video, so re-prompts and new jobs on it are instant."""
    st = Path(video).stat()
    d = CACHE / hashlib.sha1(f"{Path(video).resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_context(out_dir):
    """Everything a re-render needs, read back from a job's output folder."""
    out = Path(out_dir)
    job = json.loads((out / "job_context.json").read_text())
    info = job["info"]
    vc = Path(job.get("cache") or video_cache(job["video"]))
    tr = json.loads((vc / "transcript.json").read_text())
    an = analyze(job["video"], info, tr, vc)
    return job, info, tr, an


def base_spec(video, info, rules, settings, start, end, an, segments=None, montage=False):
    cap_over = settings.get("captions", {})
    upper = rules.get("caption_uppercase")
    segments = segments or [{"start": round(start, 3), "end": round(end, 3)}]
    word_src = [w for seg in segments for w in an.words_between(seg["start"], seg["end"])]
    spec = S.new_spec(
        source=str(video),
        segments=[{"start": round(s["start"], 3), "end": round(s["end"], 3)} for s in segments],
        # montages fade out over the action; talking clips get a short fade after the last sentence
        ending={"fade": 1.0, "to_black": 0.5} if montage else {"fade": 0.35, "to_black": 0.0},
        join={"type": "dissolve" if montage else "cut", "dur": 0.2},      # edits blend smoothly by default
        aspect=settings.get("aspect") or "9:16",
        src_size=[info["width"], info["height"]],
        fps=int(settings.get("fps") or 30),
        layout={"mode": LAYOUT_ALIASES.get(rules.get("framing"), rules.get("framing") or "auto")},
        words=[{"w": (w.get("w_roman") or w["w"]) if cap_over.get("script") == "roman" else w["w"],
                "s": round(w["s"], 3), "e": round(w["e"], 3), "deleted": False} for w in word_src],
        captions={
            "enabled": rules.get("captions_required") != "no",
            "template": settings.get("template") or "bold_pop",
            # brief rules beat template defaults only when the brief actually set them
            "font": rules.get("caption_font") if rules.get("caption_font") != DEFAULTS["caption_font"] else "",
            "color": rules.get("caption_color") if rules.get("caption_color") != DEFAULTS["caption_color"] else "",
            "highlight": rules.get("highlight_color") if rules.get("highlight_color") != DEFAULTS["highlight_color"] else "",
            "uppercase": None if upper in (None, DEFAULTS["caption_uppercase"]) else upper == "yes",
            "position": rules.get("caption_position") if rules.get("caption_position") != DEFAULTS["caption_position"] else "",
            "words_per_line": rules.get("words_per_caption") if rules.get("words_per_caption") != DEFAULTS["words_per_caption"] else 0,
            **cap_over,
        },
        watermark=rules.get("watermark_text", ""),
    )
    if settings.get("layout"):
        spec["layout"]["mode"] = LAYOUT_ALIASES.get(settings["layout"], settings["layout"])
    for k in ("audio", "logo", "frame", "join"):
        if settings.get(k):
            spec[k].update(settings[k])
    for k in ("transitions", "sharpen", "intro", "outro"):
        if k in settings:
            spec[k] = settings[k]
    if settings.get("watermark") and not spec["watermark"]:
        spec["watermark"] = settings["watermark"]
    return spec


def run(video, rules_path, out, settings=None, model=None, whisper=DEFAULT_WHISPER):
    conf = cfg.load()
    settings = cfg.apply_brand(settings or {}, cfg.get_brand(settings.get("brand") or conf.get("default_brand") or "")
                               if (settings or {}).get("brand") != "none" else None)
    model = model or conf.get("model") or None
    video = Path(video).expanduser().resolve()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    info = probe(video)

    if rules_path:
        log("Reading the campaign rules")
        rules, rules_text = load_rules(rules_path, model)
    else:
        rules, rules_text = dict(DEFAULTS, required_hashtags=[], required_mentions=[], required_phrases_in_post=[],
                                 banned_words=[], platforms=[], manual_checks=[], caption_must_mention=[],
                                 disclosure_hashtag="", max_hashtags=0, language=""), ""
    for key, val in (("num_clips", settings.get("clips")), ("min_seconds", settings.get("min")), ("max_seconds", settings.get("max"))):
        if val:
            rules[key] = int(val)
    if rules["min_seconds"] > rules["max_seconds"]:
        rules["max_seconds"] = rules["min_seconds"]
    rules["target_seconds"] = round((rules["min_seconds"] + rules["max_seconds"]) / 2) if (settings.get("min") or settings.get("max")) else 0
    (out / "rules.json").write_text(json.dumps(rules, indent=2, ensure_ascii=False))
    vc = video_cache(video)
    (out / "job_context.json").write_text(json.dumps({"video": str(video), "info": info, "rules_text": rules_text,
                                                      "settings": settings, "model": model, "cache": str(vc)}, indent=1))

    # a brief's content language wins (and keeps Whisper honest on music); otherwise it is detected
    tr = transcribe(video, vc / "transcript.json", info["has_audio"], whisper, settings.get("language") or rules.get("language"),
                    info["duration"])
    an = analyze(video, info, tr, vc)
    from .translit import needs_roman, roman_transcript
    script = (settings.get("captions") or {}).get("script") or "auto"
    if script != "native" and needs_roman(tr):          # Hindi etc.: Hinglish captions unless asked otherwise
        log("Writing Hinglish (Roman letter) captions")
        tr = roman_transcript(tr, vc / "transcript.json", model)
        an.tr = tr
        settings = {**settings, "captions": {**(settings.get("captions") or {}), "script": "roman"}}

    from .modes import MODES, apply_recipe, resolve
    mode, why = resolve(settings, tr, an, info["duration"], video.name, model)
    settings = apply_recipe(settings, mode)
    log(f"Content type: {MODES[mode]['label']} ({why})")
    ctx = json.loads((out / "job_context.json").read_text())
    ctx.update(settings=settings, mode=mode, mode_reason=why)
    (out / "job_context.json").write_text(json.dumps(ctx, indent=1))

    if settings.get("caption_only"):
        s0, s1 = float(settings.get("start") or 0), float(settings.get("end") or info["duration"])
        clips = [{"start": s0, "end": min(s1, info["duration"]), "title": "", "score": None, "reason": "Captions only",
                  "source": "caption-only"}]
        settings.setdefault("aspect", "original")
    else:
        log(f"Rules: {rules['num_clips']} clips, {rules['min_seconds']}-{rules['max_seconds']}s")
        clips = pick_clips(tr, rules, info["duration"], video, info["has_audio"], model, an=an, settings=settings)

    results = []
    for n, c in enumerate(clips, 1):
        name = f"clip_{n:02d}"
        if c.get("segments"):
            secs = sum(g["end"] - g["start"] for g in c["segments"])
            log(f"{name}: montage of {len(c['segments'])} moments ({secs:.0f}s), score {c.get('score')}")
        else:
            log(f"{name}: {c['start']:.1f}s → {c['end']:.1f}s ({c['end'] - c['start']:.0f}s), score {c.get('score')}")
        spec = base_spec(video, info, rules, settings, c["start"], c["end"], an, c.get("segments"), c.get("source") == "montage")
        if mode == "music":                                   # a song section ends on the music, not a word
            spec["ending"] = {"fade": 1.5, "to_black": 0.4}
        if settings.get("ending"):
            spec["ending"].update(settings["ending"])
        spec["meta"] = {"picked_by": c["source"], "mode": mode, "reason": c.get("reason", ""), "hook_idea": c.get("title", ""),
                        "score": c.get("score"), "score_parts": c.get("score_parts")}
        results.append(finish_clip(out, name, spec, info, an, rules, rules_text, model, settings))

    write_summary(out, results, rules)
    log(f"Done: {len(results)} clips in {out}")
    return results


def enforce_brief(spec, rules):
    """Switch off anything the brief bans, and say so. Returns the warnings."""
    warnings = []
    a = spec["audio"]
    if rules.get("allows_music") == "no" and a.get("music"):
        a["music"] = ""
        warnings.append("Background music removed: the brief bans added music.")
    if rules.get("allows_ai_voice") == "no" and a["voiceover"].get("text"):
        a["voiceover"]["text"] = ""
        warnings.append("AI voice-over removed: the brief bans AI voices.")
    if rules.get("allows_outside_footage") == "no" and spec.get("broll"):
        spec["broll"] = []
        warnings.append("B-roll removed: the brief only allows official footage.")
    if rules.get("allows_added_logos") == "no" and spec["logo"].get("path"):
        spec["logo"]["path"] = ""
        warnings.append("Logo removed: the brief bans added logos.")
    fr = spec.get("frame") or {}
    if rules.get("allows_on_screen_text") == "no" and fr.get("style", "none") != "none" and not fr.get("no_text"):
        fr["no_text"] = True
        warnings.append("Frame headline removed: the brief bans added on-screen text.")
    spec["meta"]["warnings"] = warnings
    return warnings


def apply_auto(spec, settings, an, model, rules=None):
    """Automatic edits chosen in settings: keyword/emoji captions, speaker labels, cuts, zooms."""
    from .enrich import assign_speakers, auto_zooms, enrich_words
    cap, a = spec["captions"], spec["audio"]
    assign_speakers(spec, an)
    if cap.get("enabled") and (cap.get("keywords", True) or cap.get("emoji")) and spec["words"]:
        enrich_words(spec, model, keywords=cap.get("keywords", True), emoji=cap.get("emoji", False))
    from .editor import recompute_cuts
    recompute_cuts(spec)
    if settings.get("auto_zoom"):
        auto_zooms(spec, S.output_words(spec))
    notes = []
    if settings.get("broll"):
        if (rules or {}).get("allows_outside_footage") == "no":
            notes.append("B-roll skipped: the brief only allows official footage.")
        else:
            from .broll import fill
            _, notes = fill(spec, S.output_words(spec), S.duration(spec), model,
                            sources=tuple(settings.get("broll_sources") or ("library", "pexels")))
    spec["meta"]["notes"] = notes
    return spec


def finish_clip(out, name, spec, info, an, rules, rules_text, model, settings, post=None):
    apply_auto(spec, settings, an, model, rules)
    enforce_brief(spec, rules)
    spec["meta"]["warnings"] = spec["meta"].get("warnings", []) + spec["meta"].get("notes", [])
    spoken = " ".join(w["w"] for w in S.output_words(spec))
    if post is None and not settings.get("caption_only"):
        post = write_post(spoken, spec["meta"].get("hook_idea", ""), rules, rules_text, model)
        for _ in range(2):
            if not post["missing_points"]:
                break
            post = write_post(spoken, spec["meta"].get("hook_idea", ""), rules, rules_text, model)
    post = post or {"title": "", "caption": "", "disclosure": "", "hashtags": [], "missing_points": []}
    # on-screen hook: one relatable / curious line shown for the whole clip (unless turned off or banned)
    hook_on = settings.get("hook", True) and rules.get("allows_on_screen_text") != "no" and not settings.get("caption_only")
    hook = post["title"]
    if hook_on:
        from .hooktext import write_hook
        hook = write_hook(spoken, settings.get("mode"), rules, model, spec["captions"].get("script") == "roman", post["title"])
    elif rules.get("allows_on_screen_text") == "no":
        spec["meta"]["warnings"].append("On-screen hook left off: the brief bans added on-screen text.")
    spec["title"] = {"text": hook, "show": bool(hook_on and hook) or rules.get("title_overlay") == "yes",
                     "seconds": 0 if hook_on else 4.0}                  # 0 = the whole clip
    spec["meta"]["post"] = post
    spec_path = out / f"{name}.json"
    S.save(spec_path, spec, "created", "auto")
    render(spec, an, info, out / f"{name}.mp4")
    (out / f"{name}.txt").write_text(
        f"POST CAPTION (copy everything below this line)\n{post_text(post)}\n\nON-SCREEN HOOK: {spec['title']['text']}\n",
        encoding="utf-8")
    return summary_entry(name, spec, rules)


def summary_entry(name, spec, rules):
    post = spec["meta"].get("post", {})
    spoken = " ".join(w["w"] for w in S.output_words(spec))
    return {
        "file": f"{name}.mp4", "spec": f"{name}.json",
        "start": spec["segments"][0]["start"], "end": spec["segments"][-1]["end"],
        "seconds": round(S.duration(spec), 1), "framing": spec["layout"]["mode"], "aspect": spec["aspect"],
        "picked_by": spec["meta"].get("picked_by"), "score": spec["meta"].get("score"),
        "score_parts": spec["meta"].get("score_parts"), "reason": spec["meta"].get("reason", ""),
        "title": post.get("title", ""), "caption": post.get("caption", ""), "disclosure": post.get("disclosure", ""),
        "hashtags": post.get("hashtags", []), "missing_points": post.get("missing_points", []),
        "post_text": post_text(post) if post else "",
        "banned_words_spoken": [b for b in rules.get("banned_words", []) if re.search(rf"\b{re.escape(b)}\b", spoken, re.IGNORECASE)],
        "warnings": spec["meta"].get("warnings", []),
        "v": spec.get("v"),
    }


def write_summary(out, results, rules):
    (out / "clips.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (out / "report.md").write_text(report(results, rules), encoding="utf-8")


def report(results, rules):
    ok = lambda b: "✅" if b else "❌"
    lines = ["# Clip report", "", f"Rules: {rules['num_clips']} clips, {rules['min_seconds']}–{rules['max_seconds']} s", "",
             f"Clips made: {len(results)} of {rules['num_clips']} {ok(len(results) >= rules['num_clips'])}", ""]
    for r in results:
        length_ok = rules["min_seconds"] - 1 <= r["seconds"] <= rules["max_seconds"] + 1
        tags_ok = all(t.lower() in (x.lower() for x in r["hashtags"]) for t in rules["required_hashtags"])
        mentions_ok = all(m.lower() in r["caption"].lower() for m in rules["required_mentions"])
        lines += [f"## {r['file']} — {r['title']}",
                  f"- {r['start']:.1f}s → {r['end']:.1f}s · {r['seconds']} s {ok(length_ok)} · layout: {r['framing']} · score {r['score']}",
                  f"- Required hashtags {ok(tags_ok)} · tags {ok(mentions_ok)}",
                  *([f"- Caption covers required points {ok(not r['missing_points'])}"] if rules.get("caption_must_mention") else []),
                  f"- Banned words spoken: {', '.join(r['banned_words_spoken']) or 'none'} {ok(not r['banned_words_spoken'])}", ""]
    if rules.get("manual_checks"):
        lines += ["## Check these yourself before posting", *[f"- [ ] {m}" for m in rules["manual_checks"]], ""]
    return "\n".join(lines)
