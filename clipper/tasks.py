"""Background tasks the web app runs in a subprocess:
    python -m clipper.tasks render  <spec.json> [--preview]
    python -m clipper.tasks add     <job_out> <start> <end>
    python -m clipper.tasks rescore <spec.json>
"""
import argparse
import json
from pathlib import Path

from . import spec as S
from .pipeline import base_spec, finish_clip, load_context, summary_entry, write_summary
from .rerender import rerender
from .scoring import score_clip


def add_clip(out, start, end):
    out = Path(out)
    job, info, tr, an = load_context(out)
    rules = json.loads((out / "rules.json").read_text())
    settings = job.get("settings", {})
    existing = sorted(out.glob("clip_*.json"))
    n = max([int(p.stem.split("_")[1]) for p in existing if p.stem.split("_")[1].isdigit()] + [0]) + 1
    name = f"clip_{n:02d}"
    spec = base_spec(job["video"], info, rules, settings, start, end, an)
    sc = score_clip(start, end, an, rules, job.get("model"), settings.get("prompt", ""), mode=job.get("mode"))
    spec["meta"] = {"picked_by": "manual", "reason": "Added by you", "hook_idea": "", "score": sc["total"], "score_parts": sc}
    entry = finish_clip(out, name, spec, info, an, rules, job.get("rules_text", ""), job.get("model"), settings)
    clips = json.loads((out / "clips.json").read_text()) if (out / "clips.json").exists() else []
    write_summary(out, clips + [entry], rules)
    return entry


def rescore(spec_path):
    spec_path = Path(spec_path)
    out = spec_path.parent
    job, info, tr, an = load_context(out)
    rules = json.loads((out / "rules.json").read_text())
    spec = S.load(spec_path)
    seg = spec["segments"]
    sc = score_clip(seg[0]["start"], seg[-1]["end"], an, rules, job.get("model"), job.get("settings", {}).get("prompt", ""),
                    mode=job.get("mode"))
    spec["meta"]["score"], spec["meta"]["score_parts"] = sc["total"], sc
    S.save(spec_path, spec, "re-scored", "auto")
    clips = json.loads((out / "clips.json").read_text())
    entry = summary_entry(spec_path.stem, spec, rules)
    (out / "clips.json").write_text(json.dumps([entry if c["spec"] == entry["spec"] else c for c in clips], indent=2, ensure_ascii=False))
    return sc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("spec")
    r.add_argument("--preview", action="store_true")
    a = sub.add_parser("add")
    a.add_argument("out")
    a.add_argument("start", type=float)
    a.add_argument("end", type=float)
    s = sub.add_parser("rescore")
    s.add_argument("spec")
    args = ap.parse_args()
    if args.cmd == "render":
        print(rerender(args.spec, args.preview))
    elif args.cmd == "add":
        print(json.dumps(add_clip(args.out, args.start, args.end)))
    else:
        print(json.dumps(rescore(args.spec)["total"]))
