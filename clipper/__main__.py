import argparse
import json
from pathlib import Path

from .pipeline import run
from .transcribe import DEFAULT_WHISPER


def main():
    ap = argparse.ArgumentParser(prog="clip", description="Turn a long video into short clips that follow a campaign brief.")
    ap.add_argument("video")
    ap.add_argument("--rules", help="Campaign brief: .pdf, .txt or .md (optional)")
    ap.add_argument("--out", help="Output folder (default: <video name>_clips next to the video)")
    ap.add_argument("--clips", type=int, help="Number of clips")
    ap.add_argument("--min", type=int, help="Minimum seconds per clip")
    ap.add_argument("--max", type=int, help="Maximum seconds per clip")
    ap.add_argument("--layout", "--framing", dest="layout",
                    choices=["auto", "track", "face", "fit", "center", "split", "gameplay", "screen", "manual"])
    ap.add_argument("--aspect", choices=["9:16", "1:1", "4:5", "16:9", "original"])
    ap.add_argument("--template", help="Caption template (bold_pop, karaoke, boxed, minimal, neon, word_pop, subtitle)")
    ap.add_argument("--prompt", help="Tell the AI what to clip, e.g. 'moments about pricing'")
    ap.add_argument("--start", type=float, help="Only clip from this second")
    ap.add_argument("--end", type=float, help="Only clip up to this second")
    ap.add_argument("--caption-only", action="store_true", help="Caption the whole video (or --start/--end) without clipping")
    ap.add_argument("--settings", help="JSON file with any other settings (audio, effects, brand...)")
    ap.add_argument("--model", help="Ollama model (default: best installed)")
    ap.add_argument("--whisper", default=DEFAULT_WHISPER)
    a = ap.parse_args()

    settings = json.loads(Path(a.settings).read_text()) if a.settings else {}
    for k in ("clips", "min", "max", "layout", "aspect", "template", "prompt", "start", "end"):
        v = getattr(a, k)
        if v is not None:
            settings[k] = v
    if a.caption_only:
        settings["caption_only"] = True
    video = Path(a.video).expanduser().resolve()
    out = Path(a.out).expanduser().resolve() if a.out else video.with_name(video.stem + "_clips")
    run(video, a.rules, out, settings, a.model, a.whisper)
    print((out / "report.md").read_text())


if __name__ == "__main__":
    main()
