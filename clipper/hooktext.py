"""On-screen hook: one short line of text shown from the first frame to the last.

Most viewers decide in the first seconds and watch muted, so a line that tells them why this clip
is relatable, funny or worth their time keeps them past second 3, and because it stays on screen,
anyone who scrolls in late gets it too. The line is written per content type, must describe what
really happens in the clip (briefs ban made-up or rage-bait hooks), and is checked: short, no
hashtags, no banned words, no emoji (they don't render reliably in burned-in text).
"""
import re

from .llm import ask_json

STYLE = {
    "comedy": "a RELATABLE line in the format creators use for stand-up clips, e.g. 'When mummy becomes papa's spokesperson', "
              "'POV: your dad finds your report card', 'Every Indian family has this one aunty'. It sets up the joke's "
              "situation WITHOUT giving away the punchline.",
    "podcast": "a curiosity line about the clip's key point, e.g. 'Why most startups die in year 2', "
               "'He spent 10 years learning this the hard way'. Specific, not vague.",
    "talking": "a curiosity line about the one point the clip makes, specific and useful.",
    "trailer": "the stakes or the hook of what is shown, e.g. 'The last noble of his house wants revenge'.",
    "gameplay": "what makes this moment worth watching, e.g. '1v4 clutch with 2 HP left'.",
    "music": "a short line about the song moment, e.g. 'The chorus everyone is replaying'.",
}
SCHEMA = {"type": "object", "properties": {"hook": {"type": "string"}}, "required": ["hook"]}
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def clean(text, banned=()):
    t = re.sub(r"#\w+", "", EMOJI.sub("", text or "")).strip().strip('"\'')     # whole hashtags go
    t = re.sub(r"\s+", " ", t).strip(" \"'")
    words = t.split()
    if len(words) > 12:
        t = " ".join(words[:12])
    if any(re.search(rf"\b{re.escape(b)}\b", t, re.I) for b in banned):
        return ""
    return t


def write_hook(clip_text, mode, rules, model, roman=False, fallback=""):
    """The on-screen hook line for one clip, or the fallback (the post title) if it can't be written."""
    if not (clip_text or "").strip():
        return clean(fallback, rules.get("banned_words", []))
    lang = ("Hinglish written in Roman letters (casual, the way Indian creators write)" if roman
            else "the same language as the clip, or simple English")
    try:
        out = ask_json(
            "Write the ON-SCREEN HOOK for a short vertical clip: one line of text shown at the top for the whole video. "
            f"It should be {STYLE.get(mode, STYLE['podcast'])}\n"
            f"Rules: max 9 words, in {lang}. No emoji, no hashtags, no quotes. It must be true to what happens or is said "
            "in the clip: no invented facts, numbers or claims, no rage bait, no clickbait that the clip doesn't pay off.\n"
            + (f"Never use: {', '.join(rules['banned_words'])}\n" if rules.get("banned_words") else "")
            + f"\nWHAT IS SAID IN THE CLIP:\n{clip_text[:3000]}",
            SCHEMA, model, temperature=0.7)
        hook = clean(out.get("hook", ""), rules.get("banned_words", []))
        from .llm import answered_by_fallback
        if copies_transcript(hook, clip_text) or (answered_by_fallback() and len(hook.split()) < 3):
            hook = ""                      # a weak or copied hook on screen is worse than none
    except Exception:
        hook = ""
    return hook or ("" if fallback_is_weak(fallback) else clean(fallback, rules.get("banned_words", [])))


def copies_transcript(hook, clip_text):
    """A hook that is just 5+ consecutive words of the transcript isn't a hook."""
    norm = lambda t: re.sub(r"[^\w\s]", "", t.lower()).split()
    h, words = norm(hook), " ".join(norm(clip_text))
    return len(h) >= 5 and any(" ".join(h[i:i + 5]) in words for i in range(len(h) - 4))


def fallback_is_weak(fallback):
    from .llm import answered_by_fallback
    return not fallback or answered_by_fallback()
