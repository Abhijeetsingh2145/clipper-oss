"""Built-in sound effects, synthesized in code (no third-party samples, no licensing questions),
plus any .wav/.mp3 you drop into ~/clipper/assets/sfx/."""
from pathlib import Path

import numpy as np

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"
SR = 48000


def _env(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / max(decay, 1e-4))


def _write(path, x):
    import soundfile as sf
    x = x / (np.abs(x).max() + 1e-9) * 0.8
    sf.write(str(path), np.stack([x, x], 1).astype(np.float32), SR)


def _whoosh():
    n = int(SR * 0.45)
    noise = np.random.default_rng(1).standard_normal(n)
    # sweep a resonant band-pass upward by mixing progressively higher-passed noise
    t = np.linspace(0, 1, n)
    lp = np.convolve(noise, np.ones(40) / 40, "same")
    sweep = (1 - t) * lp + t * (noise - lp)
    return sweep * np.sin(np.pi * t) ** 2


def _pop():
    n = int(SR * 0.12)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 18) + 300
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.03)


def _hit():
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    f = 120 * np.exp(-t * 6) + 45
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.18)
    click = np.random.default_rng(2).standard_normal(n) * _env(n, 0.0005, 0.01) * 0.4
    return body + click


def _riser():
    n = int(SR * 1.2)
    t = np.linspace(0, 1, n)
    noise = np.random.default_rng(3).standard_normal(n)
    tone = np.sin(2 * np.pi * np.cumsum(200 + 1200 * t ** 2) / SR)
    return (0.6 * noise * t ** 2 + 0.4 * tone * t) * np.minimum(1, (1 - t) * 30)


BUILTIN = {"whoosh": _whoosh, "pop": _pop, "hit": _hit, "riser": _riser}


def library():
    """name -> path for built-in and user sound effects (built-ins are generated on first use)."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in BUILTIN.items():
        p = SFX_DIR / f"{name}.wav"
        if not p.exists():
            _write(p, fn())
    return {p.stem: p for p in sorted(SFX_DIR.iterdir()) if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".aac")}


def auto_events(spec, pieces):
    """Where effects naturally go: whoosh on jump cuts and B-roll, pop on zooms, hit on the title."""
    ev = []
    if spec.get("transitions") == "punch":
        ev += [{"t": o, "name": "whoosh"} for _, _, o in pieces[1:]]
    ev += [{"t": z["t"], "name": "pop"} for z in spec.get("zooms", [])]
    ev += [{"t": b["t"], "name": "whoosh"} for b in spec.get("broll", [])]
    if spec.get("title", {}).get("show") and spec["title"].get("text"):
        ev.append({"t": 0.0, "name": "hit"})
    return ev
