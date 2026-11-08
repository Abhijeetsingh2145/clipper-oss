import hashlib
import json
import re
from pathlib import Path

from .llm import ask_json, default_model

TRI = {"type": "string", "enum": ["yes", "no", "unspecified"]}
STR = {"type": "string"}
INT = {"type": "integer"}
LIST = {"type": "array", "items": {"type": "string"}}

FIELDS = {
    "num_clips": (INT, "How many clips to make. 0 if not stated."),
    "min_seconds": (INT, "Minimum clip length in seconds. 0 if not stated."),
    "max_seconds": (INT, "Maximum clip length in seconds. 0 if not stated."),
    "captions_required": (TRI, "Must on-screen captions/subtitles be burned in?"),
    "caption_font": (STR, "Caption font name. Empty if not stated."),
    "caption_color": (STR, "Caption text colour (hex or name). Empty if not stated."),
    "highlight_color": (STR, "Colour of the currently spoken word. Empty if not stated."),
    "caption_uppercase": (TRI, "Must captions be ALL CAPS?"),
    "caption_position": ({"type": "string", "enum": ["top", "middle", "lower", "bottom", "unspecified"]}, "Where the burned-in CAPTIONS/SUBTITLES must sit. Not the hook title or on-screen text. unspecified if not stated."),
    "words_per_caption": (INT, "Max words shown at once. 0 if not stated."),
    "framing": ({"type": "string", "enum": ["face", "fit", "center", "unspecified"]}, "face = follow the speaker, fit = whole frame on blurred background, center = centre crop."),
    "title_overlay": (TRI, "Must a title/hook text be shown at the top of the clip?"),
    "watermark_text": (STR, "Text that must be visible on the video (handle, brand). Empty if none."),
    "disclosure_hashtag": (STR, "Sponsorship/FTC disclosure tag that must come first, e.g. #Ad. Empty if none."),
    "max_hashtags": (INT, "Most hashtags allowed in the post, counting the disclosure tag. 0 if not stated."),
    "caption_must_mention": (LIST, "Facts or messages the post caption must communicate (not exact wording), e.g. a date or offer."),
    "required_hashtags": (LIST, "Other hashtags that must be in the post, with #. Not the disclosure tag."),
    "required_mentions": (LIST, "Accounts that must be tagged, with @."),
    "required_phrases_in_post": (LIST, "Exact phrases/links that must appear in the post caption."),
    "banned_words": (LIST, "Words or topics that must not appear."),
    "language": (STR, "Two-letter code of the language clips/posts must be in, e.g. en. Empty if not stated."),
    "platforms": (LIST, "Platforms the clips are for (TikTok, Reels, Shorts...)."),
    "content_focus": (STR, "What kind of moments to pick, in the campaign's words."),
    "post_caption_style": (STR, "How the post caption/description should be written."),
    "allows_music": (TRI, "Does the brief allow adding background music? 'no' if it bans added music."),
    "allows_ai_voice": (TRI, "Does the brief allow AI voice-overs or AI narration? 'no' if banned."),
    "allows_outside_footage": (TRI, "May footage or images from other sources (stock, B-roll) be added? 'no' if only official footage is allowed."),
    "allows_added_logos": (TRI, "May logos or brand graphics be added on screen? 'no' if banned."),
    "allows_on_screen_text": (TRI, "May text be added on screen (titles, headlines)? 'no' only if the brief forbids it."),
    "manual_checks": (LIST, "Every rule this tool cannot do by itself (posting times, account rules, sounds, approvals, editing styles it can't do). Quote each rule briefly."),
}

SCHEMA = {
    "type": "object",
    "properties": {k: v[0] for k, v in FIELDS.items()},
    "required": list(FIELDS),
}

DEFAULTS = {
    "num_clips": 5, "min_seconds": 20, "max_seconds": 60,
    "captions_required": "yes", "caption_font": "Arial Black", "caption_color": "#FFFFFF",
    "highlight_color": "#FFE600", "caption_uppercase": "yes", "caption_position": "lower",
    "words_per_caption": 3, "framing": "auto", "title_overlay": "no",
}


# Used when a brief bans "profanity" in general rather than listing words.
PROFANITY = ["fuck", "shit", "bitch", "asshole", "bastard", "dick", "pussy", "cunt", "motherfucker", "damn", "nigga", "slut", "whore"]


def read_rules_text(path):
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((page.extract_text() or "") for page in PdfReader(p).pages)
        if len(text.strip()) < 20:
            raise SystemExit("The rules PDF has no readable text (it may be a scanned image). Export it as a text PDF or paste the rules into a .txt file.")
        return text
    return p.read_text()


CACHE = Path(__file__).resolve().parent.parent / "cache" / "rules"


def load_rules(path, model):
    text = read_rules_text(path)
    field_help = "\n".join(f"- {k}: {v[1]}" for k, v in FIELDS.items())
    key = hashlib.sha1(f"{model or default_model()}|{field_help}|{text}".encode()).hexdigest()[:16]
    cached = CACHE / f"{key}.json"
    if cached.exists():                       # same brief + same model: reuse the reading
        extracted = json.loads(cached.read_text())
    else:
        extracted = ask_json(
            "Read this clipping campaign brief and fill in every field. Only use what the brief says; "
            "use 0, empty string, empty list or 'unspecified' when it says nothing. Never invent rules.\n\n"
            f"Fields:\n{field_help}\n\nBRIEF:\n{text}",
            SCHEMA, model, temperature=0,
        )
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(extracted, ensure_ascii=False))
    rules = dict(DEFAULTS)
    for k, v in extracted.items():
        if v in (0, "", [], "unspecified", None):
            continue
        rules[k] = v
    for k in ("required_hashtags", "required_mentions", "required_phrases_in_post", "banned_words", "platforms",
              "manual_checks", "caption_must_mention"):
        rules.setdefault(k, [])
    rules["language"] = (rules.get("language") or "").strip().lower()[:2]
    rules.setdefault("disclosure_hashtag", "")
    rules.setdefault("max_hashtags", 0)
    # models sometimes return every allowed option ("#Ad, Advertisement, Sponsored"); one tag is the rule
    first = re.findall(r"#?([A-Za-z][\w]*)", rules["disclosure_hashtag"] or "")
    rules["disclosure_hashtag"] = f"#{first[0]}" if first else ""
    mentions = {m.lstrip("@").lower() for m in rules["required_mentions"]}
    # Briefs list disclosure alternatives ("#Ad #Advertisement #Sponsored"); only one is needed.
    alt = {"ad", "ads", "advertisement", "sponsored", "paid", "paidpartnership", "partner", "promo"}
    rules["required_hashtags"] = [h for h in rules["required_hashtags"]
                                  if "@" not in h and h.lstrip("#").lower() not in mentions
                                  and not (rules["disclosure_hashtag"] and h.lstrip("#").lower() in alt)
                                  and h.lstrip("#").lower() != rules["disclosure_hashtag"].lstrip("#").lower()]
    # Points the caption must *say* are handled by the writer; only keep exact strings (links, codes) as phrases.
    must = " | ".join(rules["caption_must_mention"]).lower()
    rules["required_phrases_in_post"] = [p for p in rules["required_phrases_in_post"]
                                         if p.lower() not in must and p.lstrip("@").lower() not in mentions]
    rules["required_hashtags"] = [h if h.startswith("#") else f"#{h}" for h in rules["required_hashtags"]]
    rules["required_mentions"] = [m if m.startswith("@") else f"@{m}" for m in rules["required_mentions"]]
    if any(w in b.lower() for b in rules["banned_words"] for w in ("profan", "swear", "curse", "cuss")):
        rules["banned_words"] = [b for b in rules["banned_words"] if not any(w in b.lower() for w in ("profan", "swear", "curse", "cuss"))] + PROFANITY
    if rules["min_seconds"] > rules["max_seconds"]:
        rules["max_seconds"] = rules["min_seconds"] + 30
    return rules, text
