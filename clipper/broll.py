"""B-roll: short cutaways that illustrate what's being said.

The language model picks moments + a visual search phrase; media comes from your own library
folder first (matched on file/folder names), then Pexels (free stock, needs your API key).
Every insert records where it came from. Off unless enabled, and stripped automatically when a
brief only allows official footage.
"""
import re
from pathlib import Path

import requests

from . import settings as cfg
from .llm import ask_json

CACHE = cfg.ROOT / "cache" / "broll"
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png", ".webp"}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"inserts": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"}, "query": {"type": "string"}}, "required": ["i", "query"]}}},
    "required": ["inserts"],
}


def plan(out_words, total, model=None, every=7.0, dur=2.4):
    """Pick moments (word index) and a 1-3 word visual search phrase for each."""
    if not out_words:
        return []
    numbered = " ".join(f"{i}:{w['w']}" for i, w in enumerate(out_words))
    want = max(1, int(total // every))
    reply = ask_json(
        f"This is a short clip's speech, each word prefixed by its index. Choose up to {want} moments where a short "
        "illustrative cutaway would help (concrete things: objects, places, actions, money, devices). "
        "For each give the index of the word where it should start and a 1-3 word stock-footage search query. "
        "Skip abstract moments; fewer good inserts beat many vague ones. Never pick the first 2 seconds.\n\n" + numbered,
        PLAN_SCHEMA, model, temperature=0.2)
    picks, last = [], -1e9
    for ins in reply.get("inserts", []):
        i = ins.get("i", -1)
        if not 0 <= i < len(out_words):
            continue
        t = out_words[i]["s"]
        if t < 2.0 or t - last < every * 0.7 or t + dur > total - 0.5:
            continue
        picks.append({"t": round(t, 2), "dur": dur, "query": ins["query"].strip()[:60]})
        last = t
    return picks


def _tokens(s):
    return {t for t in re.findall(r"[a-z]{3,}", s.lower())}


def from_library(query, library=None):
    root = Path(library or cfg.load()["broll_library"])
    if not root.is_dir():
        return None
    q = _tokens(query)
    best, best_k = None, 0
    for p in root.rglob("*"):
        if p.suffix.lower() in MEDIA_EXT:
            k = len(q & _tokens(str(p.relative_to(root))))
            if k > best_k:
                best, best_k = p, k
    return best


def from_pexels(query, key, portrait=True):
    """Search Pexels videos (then photos) and cache the best match locally."""
    CACHE.mkdir(parents=True, exist_ok=True)
    h = {"Authorization": key}
    r = requests.get("https://api.pexels.com/videos/search", headers=h, timeout=20,
                     params={"query": query, "per_page": 6, "orientation": "portrait" if portrait else "landscape"})
    if r.ok:
        for v in r.json().get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 720]
            if not files:
                continue
            f = min(files, key=lambda f: abs((f.get("height") or 0) - 1920))
            dest = CACHE / f"pexels_v{v['id']}_{f.get('height')}.mp4"
            if not dest.exists():
                _download(f["link"], dest)
            return dest, f"Pexels video by {v.get('user', {}).get('name', 'unknown')} — {v.get('url', '')}"
    r = requests.get("https://api.pexels.com/v1/search", headers=h, timeout=20,
                     params={"query": query, "per_page": 3, "orientation": "portrait" if portrait else "landscape"})
    if r.ok:
        for ph in r.json().get("photos", []):
            src = ph.get("src", {}).get("large2x") or ph.get("src", {}).get("original")
            if src:
                dest = CACHE / f"pexels_p{ph['id']}.jpg"
                if not dest.exists():
                    _download(src, dest)
                return dest, f"Pexels photo by {ph.get('photographer', 'unknown')} — {ph.get('url', '')}"
    return None


def _download(url, dest):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def fill(spec, out_words, total, model=None, sources=("library", "pexels")):
    """Plan inserts and attach media. Returns (inserts, notes about anything that couldn't be filled)."""
    conf = cfg.load()
    inserts, notes = [], []
    portrait = spec.get("aspect", "9:16") in ("9:16", "4:5")
    for p in plan(out_words, total, model):
        media, source = None, ""
        if "library" in sources:
            hit = from_library(p["query"], conf["broll_library"])
            if hit:
                media, source = hit, f"Your library: {hit.name}"
        if media is None and "pexels" in sources and conf.get("pexels_key"):
            try:
                got = from_pexels(p["query"], conf["pexels_key"], portrait)
                if got:
                    media, source = got
            except requests.RequestException as e:
                notes.append(f"Pexels search failed for '{p['query']}': {e.__class__.__name__}")
        if media is None:
            notes.append(f"No media found for '{p['query']}'" + ("" if conf.get("pexels_key") else " (add a Pexels key in Settings for stock)"))
            continue
        kind = "image" if Path(media).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else "video"
        inserts.append({**p, "path": str(media), "kind": kind, "source": source})
    spec["broll"] = inserts
    return inserts, notes
