"""App-wide settings (API keys, folders, defaults) and brand kits, stored as JSON in ~/clipper."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "settings.json"
BRANDS = ROOT / "brands"
FONTS = ROOT / "assets" / "fonts"
BROLL_LIBRARY = ROOT / "assets" / "broll"
MUSIC_LIBRARY = ROOT / "assets" / "music"
LOGOS = ROOT / "assets" / "logos"

DEFAULTS = {
    "model": "",                       # empty = best installed local; "cloud:<id>" = a gateway model
    "cloud_url": "",                   # OpenAI-compatible gateway, e.g. OmniRoute http://localhost:20128/v1
    "cloud_key": "",
    "pexels_key": "",
    "broll_library": str(BROLL_LIBRARY),
    "music_library": str(MUSIC_LIBRARY),
    "youtube_client_secret": "",       # path to the OAuth client JSON from Google Cloud
    "default_brand": "",
    "creator_name": "",                # shown on the "Social post" reel frame
    "creator_handle": "",
}
SECRET_KEYS = {"pexels_key", "cloud_key"}


def load():
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        data.update(json.loads(SETTINGS_FILE.read_text()))
    return data


def save(updates):
    data = load()
    data.update({k: v for k, v in updates.items() if k in DEFAULTS})
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    SETTINGS_FILE.chmod(0o600)          # holds API keys
    return data


def public():
    """Settings safe to send to the browser: secrets are reported as set/unset only."""
    d = load()
    return {k: (bool(v) if k in SECRET_KEYS else v) for k, v in d.items()}


for d in (BRANDS, FONTS, BROLL_LIBRARY, MUSIC_LIBRARY, LOGOS):
    d.mkdir(parents=True, exist_ok=True)


# ---------- brand kits ----------
BRAND_FIELDS = {
    "captions": {},                    # template, font, color, highlight, uppercase, position, words_per_line, emoji...
    "logo": {},                        # path, pos, scale, opacity, animate
    "watermark": "",
    "intro": "", "outro": "",
    "aspect": "", "layout": "",
    "transitions": "", "auto_zoom": None, "sharpen": None,
    "audio": {},                       # e.g. music, music_volume, enhance, denoise, remove_fillers...
    "frame": {},                       # reel frame: style (headline/cinema/card), progress, bg, fg
}


def _slug(name):
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "brand"


def list_brands():
    return [json.loads(p.read_text()) for p in sorted(BRANDS.glob("*.json"))]


def get_brand(name):
    p = BRANDS / f"{_slug(name)}.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_brand(kit):
    kit = {"name": kit.get("name") or "Brand", **{k: kit.get(k, v) for k, v in BRAND_FIELDS.items()}}
    (BRANDS / f"{_slug(kit['name'])}.json").write_text(json.dumps(kit, indent=2))
    return kit


def delete_brand(name):
    (BRANDS / f"{_slug(name)}.json").unlink(missing_ok=True)


def apply_brand(settings, kit):
    """Brand values become defaults; anything set explicitly for this job still wins."""
    if not kit:
        return settings
    out = dict(settings)
    for k in ("aspect", "layout", "transitions", "watermark", "intro", "outro"):
        if kit.get(k) and not out.get(k):
            out[k] = kit[k]
    for k in ("auto_zoom", "sharpen"):
        if kit.get(k) is not None and k not in out:
            out[k] = kit[k]
    for k in ("captions", "logo", "audio", "frame"):
        if kit.get(k):
            out[k] = {**kit[k], **out.get(k, {})}
    if kit["captions"].get("font_dir") is None and any(FONTS.iterdir()):
        out.setdefault("captions", {}).setdefault("font_dir", str(FONTS))
    return out
