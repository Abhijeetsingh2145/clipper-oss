"""Re-render one clip from its (edited) spec: python -m clipper.rerender <job_out>/clip_01.json [--preview]"""
import argparse
import json
from pathlib import Path

from . import spec as S
from .compose import render
from .pipeline import load_context, summary_entry


def rerender(spec_path, preview=False):
    spec_path = Path(spec_path)
    out = spec_path.parent
    job, info, tr, an = load_context(out)
    spec = S.load(spec_path)
    target = out / (f"{spec_path.stem}.preview.mp4" if preview else f"{spec_path.stem}.mp4")
    render(spec, an, info, target, preview=preview)
    if not preview:
        rules = json.loads((out / "rules.json").read_text())
        clips_file = out / "clips.json"
        clips = json.loads(clips_file.read_text()) if clips_file.exists() else []
        entry = summary_entry(spec_path.stem, spec, rules)
        clips = [entry if c["spec"] == entry["spec"] else c for c in clips] if any(c.get("spec") == entry["spec"] for c in clips) else clips + [entry]
        clips_file.write_text(json.dumps(clips, indent=2, ensure_ascii=False))
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    print(rerender(a.spec, a.preview))
