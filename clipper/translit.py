"""Hinglish captions: Hindi (and other Devanagari) speech shown in Roman letters, the way people
actually type it on Reels ("poore saal usi ke context mein baat"), not in Devanagari.

Each spoken word keeps its own timing, so the romanized text must stay ONE word for ONE word.
The language model writes natural Hinglish line by line (English loanwords spelled as English);
any line where it returns a different word count, or when no model is reachable, falls back to a
rule-based transliteration (ITRANS, simplified to casual spelling). Results are cached in the
transcript so a video is romanized once.
"""
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": {
    "i": {"type": "integer"}, "words": {"type": "array", "items": {"type": "string"}}}, "required": ["i", "words"]}}},
    "required": ["lines"]}


def needs_roman(tr):
    return any(DEVANAGARI.search(w["w"]) for w in tr.get("words", [])[:200])


def rule_based(word):
    """Casual romanization of one Devanagari word (keeps punctuation)."""
    if not DEVANAGARI.search(word):
        return word
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    punct = r"[^\wऀ-ॿ]*"                 # vowel signs are "non-word" to \W; never strip them
    pre = re.match("^" + punct, word).group(0)
    post = re.search(punct + "$", word).group(0)
    core = word[len(pre):len(word) - len(post)] if post else word[len(pre):]
    t = transliterate(core.replace("ॉ", "ो").replace("ऑ", "ओ"), sanscript.DEVANAGARI, sanscript.ITRANS)
    t = t.replace("A", "aa").replace("I", "ee").replace("U", "oo").replace("RRi", "ri").replace("~N", "n").replace("~n", "n")
    t = re.sub(r"\.N|\.n|M|\.m", "n", t).replace(".", "")
    t = t.replace("ch", "ch").replace("Ch", "chh").replace("sh", "sh").replace("Sh", "sh").replace("j~n", "gy")
    t = t.lower()
    if len(t) > 2 and t.endswith("a") and not t.endswith("aa") and re.search(r"[^aeiou]a$", t):
        t = t[:-1]                                   # Hindi drops the final inherent vowel: "saala" -> "saal"
    if len(t) > 2 and t.endswith("aa"):
        t = t[:-1]                                   # casual spelling: "kahaa" -> "kaha", "naa" -> "na"
    if len(t) > 2 and t.endswith("ee"):
        t = t[:-2] + "i"                             # "usee" -> "usi", "kee" -> "ki"
    return pre + t + post


def _model_lines(lines, model):
    from .llm import ask_json
    numbered = "\n".join(f"{i}. " + " | ".join(ws) for i, ws in enumerate(lines))
    out = ask_json(
        "Romanize these Hindi caption lines into casual Hinglish exactly as Indian creators type on Instagram "
        "(e.g. 'yaar', 'kya scene hai', 'mummy', 'papa'). English loanwords written in Devanagari go back to English "
        "spelling ('कॉंटेक्स' -> 'context'). Words are separated by ' | '. Return EXACTLY the same number of words per "
        "line, in the same order, one romanized word per original word, keeping punctuation. Do not translate.\n\n" + numbered,
        SCHEMA, model, temperature=0)
    return {int(r["i"]): r["words"] for r in out.get("lines", [])}


def romanize_words(words, model=None, batch=40):
    """Returns a list of romanized strings, same length as words."""
    lines, idx, cur, cur_i = [], [], [], []
    for i, w in enumerate(words):
        cur.append(w["w"])
        cur_i.append(i)
        if w["w"][-1:] in ".?!|।" or len(cur) >= 12:
            lines.append(cur)
            idx.append(cur_i)
            cur, cur_i = [], []
    if cur:
        lines.append(cur)
        idx.append(cur_i)
    result = [rule_based(w["w"]) for w in words]
    for b in range(0, len(lines), batch):
        chunk = lines[b:b + batch]
        try:
            got = _model_lines(chunk, model)
        except Exception:
            continue                                 # rule-based spelling stays for this batch
        for j, ws in enumerate(chunk):
            r = got.get(j)
            if r and len(r) == len(ws) and not any(DEVANAGARI.search(x) for x in r):
                for k, text in zip(idx[b + j], r):
                    result[k] = text.strip() or result[k]
    return result


def roman_transcript(tr, cache_path=None, model=None):
    """Adds 'w_roman' to every transcript word (cached in the transcript file)."""
    words = tr.get("words", [])
    if not words or all("w_roman" in w for w in words):
        return tr
    for w, r in zip(words, romanize_words(words, model)):
        w["w_roman"] = r
    if cache_path:
        import json
        from pathlib import Path
        Path(cache_path).write_text(json.dumps(tr, ensure_ascii=False))
    return tr
