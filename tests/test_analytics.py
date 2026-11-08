import json

import numpy as np

from clipper import analytics


def test_calibration_learns_what_drives_views(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "LOG", tmp_path / "analytics.json")
    monkeypatch.setattr(analytics, "WEIGHTS_FILE", tmp_path / "weights.json")
    rng = np.random.default_rng(0)
    rows = []
    for i in range(40):
        parts = {k: float(rng.uniform(20, 100)) for k in analytics.PARTS}
        views = int(np.expm1(parts["hook"] / 12 + rng.normal(0, 0.3)))   # only the hook matters here
        rows.append({"id": str(i), "parts": parts, "views": views, "followers": None, "score": 50})
    (tmp_path / "analytics.json").write_text(json.dumps(rows))

    rep = analytics.calibrate()
    assert rep["ok"] and rep["n"] == 40
    assert max(rep["weights"], key=rep["weights"].get) == "hook"
    assert rep["rank_correlation_after"] > rep["rank_correlation_before"]
    assert abs(sum(rep["weights"].values()) - 100) < 0.5


def test_calibration_refuses_with_too_little_data(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "LOG", tmp_path / "analytics.json")
    (tmp_path / "analytics.json").write_text("[]")
    rep = analytics.calibrate()
    assert not rep["ok"] and "at least" in rep["message"]
