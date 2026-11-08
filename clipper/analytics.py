"""Performance log for posted clips, and calibration of the quality score against real results.

Calibration (documented, deliberately simple):
  outcome  = log(1 + views / max(followers, 100))   (views relative to account size; raw views if unknown)
  features = the 7 score parts (0-100) recorded when the clip was made
  fit      = least squares on standardized features, negative weights set to 0, rescaled to sum 100
It needs >= 20 posted clips with views. The report shows how well the old and new weights rank
your clips (Spearman correlation with the outcome), so you can see whether calibration helped.
"""
import json
import re
import time
import uuid

import numpy as np

from . import settings as cfg
from .scoring import DEFAULT_WEIGHTS, WEIGHTS_FILE

LOG = cfg.ROOT / "analytics.json"
MIN_FOR_CALIBRATION = 20
PARTS = list(DEFAULT_WEIGHTS)


def _load():
    return json.loads(LOG.read_text()) if LOG.exists() else []


def _save(items):
    LOG.write_text(json.dumps(items, indent=1, ensure_ascii=False))


def record(job, clip, platform, url, score_parts, campaign="", niche="", followers=None):
    items = _load()
    item = {"id": uuid.uuid4().hex[:10], "job": job, "clip": clip, "platform": platform, "url": url,
            "campaign": campaign, "niche": niche, "followers": followers, "posted_at": time.time(),
            "parts": {k: v["score"] for k, v in (score_parts or {}).get("parts", {}).items()},
            "score": (score_parts or {}).get("total"), "views": None, "likes": None, "comments": None, "shares": None,
            "updated_at": None}
    items.append(item)
    _save(items)
    return item


def update(item_id, **stats):
    items = _load()
    for it in items:
        if it["id"] == item_id:
            for k in ("views", "likes", "comments", "shares", "followers", "url", "campaign", "niche"):
                if k in stats and stats[k] not in (None, ""):
                    it[k] = int(stats[k]) if k in ("views", "likes", "comments", "shares", "followers") else stats[k]
            it["updated_at"] = time.time()
            _save(items)
            return it
    raise KeyError(item_id)


def delete(item_id):
    _save([i for i in _load() if i["id"] != item_id])


def sync_youtube():
    """Pull view/like/comment counts for logged YouTube posts."""
    from .publish import youtube_stats
    items = _load()
    ids = {}
    for it in items:
        m = re.search(r"(?:shorts/|v=|youtu\.be/)([\w-]{11})", it.get("url") or "")
        if it["platform"] == "youtube" and m:
            ids[m.group(1)] = it
    stats = youtube_stats(ids)
    for vid, s in stats.items():
        ids[vid].update(s, updated_at=time.time())
    _save(items)
    return len(stats)


def _outcome(it):
    f = it.get("followers")
    return np.log1p(it["views"] / max(f, 100)) if f else np.log1p(it["views"])


def _spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 2 and np.std(ra) and np.std(rb) else 0.0


def calibrate(dry_run=False):
    rows = [i for i in _load() if i.get("views") is not None and all(k in i.get("parts", {}) for k in PARTS)]
    if len(rows) < MIN_FOR_CALIBRATION:
        return {"ok": False, "n": len(rows), "message": f"Need at least {MIN_FOR_CALIBRATION} posted clips with views (have {len(rows)})."}
    X = np.array([[r["parts"][k] for k in PARTS] for r in rows], dtype=float)
    y = np.array([_outcome(r) for r in rows])
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    coef, *_ = np.linalg.lstsq(np.c_[Z, np.ones(len(Z))], y, rcond=None)
    w = np.clip(coef[:-1] / sd, 0, None)              # back to raw-score units, no negative weights
    if w.sum() <= 0:
        return {"ok": False, "n": len(rows), "message": "None of the score parts predicted your results; keeping current weights."}
    w = w / w.sum() * 100
    new = {k: round(float(v), 1) for k, v in zip(PARTS, w)}
    old = json.loads(WEIGHTS_FILE.read_text())["weights"] if WEIGHTS_FILE.exists() else DEFAULT_WEIGHTS
    score = lambda wt: X @ np.array([wt[k] for k in PARTS]) / sum(wt.values())
    report = {"ok": True, "n": len(rows), "weights": new,
              "rank_correlation_before": round(_spearman(score(old), y), 3),
              "rank_correlation_after": round(_spearman(score(new), y), 3)}
    if not dry_run:
        WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEIGHTS_FILE.write_text(json.dumps({"weights": new, "n": len(rows), "fitted_at": time.time(), "report": report}, indent=1))
    return report


def summary():
    items = _load()
    with_views = [i for i in items if i.get("views") is not None]
    by = {}
    for i in with_views:
        key = i.get("campaign") or i.get("niche") or "Uncategorized"
        g = by.setdefault(key, {"posts": 0, "views": 0, "likes": 0})
        g["posts"] += 1
        g["views"] += i["views"] or 0
        g["likes"] += i.get("likes") or 0
    for g in by.values():
        g["avg_views"] = round(g["views"] / g["posts"])
        g["engagement"] = round(100 * g["likes"] / g["views"], 2) if g["views"] else 0
    corr = _spearman([i["score"] or 0 for i in with_views], [_outcome(i) for i in with_views]) if len(with_views) >= 5 else None
    return {"items": items, "groups": by, "score_vs_views_rank_correlation": corr,
            "calibrated_on": json.loads(WEIGHTS_FILE.read_text())["n"] if WEIGHTS_FILE.exists() else 0}
