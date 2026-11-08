"""Caption enrichment: keyword highlights, emoji, speaker labels, auto zoom points.

Keywords/emoji come from the language model (one call per clip, editable afterwards).
Speaker labels come from the analysis: whoever's mouth is moving when the word is said.
"""
import re

from .llm import ask_json

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "integer"}},
        "emoji": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "e": {"type": "string"}}, "required": ["i", "e"]}},
    },
    "required": ["keywords", "emoji"],
}
GENERIC = {"talk", "need", "know", "think", "say", "said", "get", "got", "go", "going", "make", "made", "thing", "things",
            "much", "many", "really", "very", "just", "start", "want", "people", "way", "time", "lot", "kind", "something",
            "let", "lets", "look", "come", "take", "see", "tell", "good", "well", "okay", "yeah", "right", "actually",
            "this", "that", "it", "these", "those", "here", "there", "what", "doing", "do", "does", "did", "be", "is", "are",
            "the", "a", "an", "to", "of", "and", "or", "in", "on", "at", "for", "with", "you", "i", "we", "my", "your"}
NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "hundred",
                "thousand", "million", "billion", "twenty", "thirty", "forty", "fifty", "percent"}


def _bare(w):
    return re.sub(r"[^\w]", "", w.lower())


def _is_number(w):
    b = _bare(w)
    return bool(re.fullmatch(r"\d[\d,.]*k?|\$\d.*", w.strip(".,!?").lower())) or b in NUMBER_WORDS - {"one"}


EMOJI_RE =re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆↔-↪]")


def enrich_words(spec, model=None, keywords=True, emoji=True):
    """Mark the words worth highlighting and attach a fitting emoji to a few of them."""
    words = [w for w in spec["words"] if not w.get("deleted")]
    if not words or not (keywords or emoji):
        return spec
    numbered = " ".join(f"{i}:{w['w']}" for i, w in enumerate(words))
    per = max(1, len(words) // 12)
    reply = ask_json(
        "These are the spoken words of a short video clip, each prefixed with its index.\n"
        f"keywords: indices of the {max(2, len(words) // 8)} words that carry the MEANING (the topic nouns, names, "
        "numbers, money, emotional or surprising words). Never pick generic verbs like talk/need/know/get or filler words.\n"
        f"emoji: about {max(1, len(words) // (12 * per))} emoji, each attached to the index of the word it illustrates. "
        "Only use an emoji when it clearly matches the meaning; return fewer rather than random ones.\n\n" + numbered,
        ENRICH_SCHEMA, model, temperature=0.2,
    )
    kw = set()
    if keywords:
        kw = {i for i in reply.get("keywords", []) if 0 <= i < len(words) and _bare(words[i]["w"]) not in GENERIC}
        kw |= {i for i, w in enumerate(words) if _is_number(w["w"])}      # numbers always pop
    em = {}
    if emoji:
        for item in reply.get("emoji", []):
            ch = "".join(EMOJI_RE.findall(item.get("e", "")))[:2]
            if ch and 0 <= item["i"] < len(words):
                em[item["i"]] = ch
    for i, w in enumerate(words):
        w["kw"] = i in kw
        w["emoji"] = em.get(i, "")
    return spec


def assign_speakers(spec, an):
    """Label each word with a speaker: the visible face whose mouth moves most while it's said.
    Labels are by screen side (left person = 0, right = 1) so they stay stable across camera cuts."""
    for w in spec["words"]:
        rows = an.faces_between(w["s"] - 0.2, w["e"] + 0.2)
        act = {}
        for r in rows:
            for d in r["d"]:
                side = 0 if d[0] + d[2] / 2 < 0.5 else 1
                act[side] = act.get(side, 0) + d[6]
        w["spk"] = max(act, key=act.get) if act and max(act.values()) > 0.05 else None
    return spec


def auto_zooms(spec, out_words, every=6.0, scale=1.12):
    """Zoom-ins on highlighted words, at most one per `every` seconds (the classic short-form emphasis)."""
    zooms, last = [], -1e9
    for w in out_words:
        if w.get("kw") and w["s"] - last >= every:
            zooms.append({"t": round(max(0, w["s"] - 0.1), 2), "dur": 1.6, "scale": scale})
            last = w["s"]
    spec["zooms"] = zooms
    return spec


FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm", "uhm", "umm", "uhh"}


def filler_cuts(words):
    """Source-time ranges of filler words ('um', 'uh'…)."""
    return [[w["s"], w["e"]] for w in words if re.sub(r"[^\w]", "", w["w"].lower()) in FILLERS]


def pause_cuts(words, seg_start, seg_end, keep=0.35):
    """Shorten every silence longer than `keep` seconds down to `keep`."""
    cuts, prev = [], seg_start
    for w in words + [{"s": seg_end, "e": seg_end}]:
        gap = w["s"] - prev
        if gap > keep:
            cuts.append([round(prev + keep / 2, 3), round(w["s"] - keep / 2, 3)])
        prev = max(prev, w["e"])
    return cuts


STOP = {"the", "a", "an", "and", "i", "you", "it", "to", "of", "is", "that", "so", "know", "like", "in", "we", "was", "do"}


def retake_cuts(words, window=15.0, min_len=4):
    """Repeated takes: when a run of >= min_len words is said again within `window` seconds,
    the earlier attempt is cut and the last take is kept."""
    norm = [re.sub(r"[^\w]", "", w["w"].lower()) for w in words]
    cuts = []
    i = 0
    while i < len(words) - min_len:
        gram = norm[i:i + min_len]
        if sum(g not in STOP for g in gram) < 2:      # "you know, you know" is speech, not a retake
            i += 1
            continue
        j = i + min_len
        found = None
        while j <= len(words) - min_len and words[j]["s"] - words[i]["s"] <= window:
            if norm[j:j + min_len] == gram:
                found = j
            j += 1
        if found is not None:
            cuts.append([words[i]["s"], words[found]["s"] - 0.05])
            i = found
        else:
            i += 1
    return cuts
