import re

from .llm import ask_json

LANGS = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "hi": "Hindi",
         "it": "Italian", "ja": "Japanese", "ko": "Korean"}
DISCLOSURE_TAGS = {"#ad", "#ads", "#advertisement", "#sponsored", "#paid", "#paidpartnership", "#partner", "#promo"}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "caption", "hashtags"],
}



def write_post(clip_text, clip_title, rules, rules_text, model):
    spoken = clip_text or "(no speech in this clip: it is music, gameplay or visuals)"
    must = "; ".join(rules["caption_must_mention"]) or "nothing specific"
    reply = ask_json(
        "Write the social media post for one short vertical clip. Follow the campaign brief exactly.\n"
        f"Platforms: {', '.join(rules['platforms']) or 'TikTok, Instagram Reels, YouTube Shorts'}\n"
        f"Caption style required: {rules.get('post_caption_style') or 'short, genuine, written like a real fan, 1-2 sentences'}\n"
        f"The caption MUST clearly say: {must}\n"
        f"Tag these accounts inside the caption: {' '.join(rules['required_mentions']) or 'none'}\n"
        f"Must never use: {', '.join(rules['banned_words']) or 'nothing specified'}\n"
        f"Write everything in {LANGS.get(rules.get('language'), 'English')}.\n"
        "Only state facts found in the brief or the clip. No invented facts, numbers or dates, no exaggerated or rage-bait hooks. "
        "Do not copy the brief's example captions word for word.\n"
        "The caption is about WHAT HAPPENS OR IS SAID IN THIS CLIP. Never describe the campaign itself or repeat the "
        "brief's instructions (e.g. don't write 'motivational moments' or 'strong opinions'); a viewer should want to watch.\n"
        "Return: a short on-screen hook title (max 8 words) that matches what happens in the clip, "
        "the post caption (no hashtags in it), and up to 4 relevant hashtags.\n\n"
        f"CAMPAIGN BRIEF:\n{rules_text[:6000]}\n\nCLIP HOOK IDEA: {clip_title}\nWHAT IS SAID IN THE CLIP:\n{spoken[:3000]}",
        SCHEMA, model, temperature=0.6,
    )
    # hashtags belong on their own lines below the caption, never inside it
    caption = re.sub(r"\s*#\w+", "", reply["caption"]).strip()
    extras = [m for m in rules["required_mentions"] if m.lower() not in caption.lower()]
    extras += [p for p in rules["required_phrases_in_post"] if p.lower() not in caption.lower()]
    if extras:
        caption = caption + " " + " ".join(extras)

    disclosure = rules.get("disclosure_hashtag", "")
    tags = list(rules["required_hashtags"])
    for t in reply["hashtags"]:
        t = "#" + t.strip().lstrip("#").replace(" ", "")
        if disclosure and t.lower() in DISCLOSURE_TAGS:
            continue  # one disclosure tag is enough; extra slots go to content tags
        if len(t) > 1 and t.lower() not in (x.lower() for x in tags + [disclosure]):
            tags.append(t)
    limit = rules.get("max_hashtags") or 0
    if limit:
        tags = tags[: max(len(rules["required_hashtags"]), limit - (1 if disclosure else 0))]
    return {"title": reply["title"].strip() or clip_title, "caption": caption, "disclosure": disclosure, "hashtags": tags,
            "missing_points": check_post(caption, rules)}


def post_text(post):
    """The text to paste into TikTok/Reels: caption, then disclosure on its own line, then hashtags."""
    lines = [post["caption"]]
    if post["disclosure"]:
        lines.append(post["disclosure"])
    if post["hashtags"]:
        lines.append(" ".join(post["hashtags"]))
    return "\n".join(lines)


def check_post(caption, rules):
    """A point counts as covered when all its key words (and numbers) appear in the caption.
    Deliberately not an LLM call: a small local model 'corrects' facts it shouldn't."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower().replace("-", ""))
    text = f" {norm(caption)} "
    missing = []
    for point in rules["caption_must_mention"]:
        keys = [w for w in norm(point).split() if len(w) >= 3 or w.isdigit()]
        if any(f" {k} " not in text for k in keys):
            missing.append(point)
    return missing
