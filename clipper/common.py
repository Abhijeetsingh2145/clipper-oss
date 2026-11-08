import json
import os
import shutil
import subprocess
import time

# Homebrew's plain ffmpeg has no libass; ffmpeg-full (keg-only) is needed to burn captions.
os.environ["PATH"] = "/opt/homebrew/opt/ffmpeg-full/bin" + os.pathsep + os.environ.get("PATH", "")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

OUT_W, OUT_H = 1080, 1920


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=True,
    ).stdout
    d = json.loads(out)
    streams = d.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video"), None)
    if not v:
        raise SystemExit(f"No video stream in {path}")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []):
        rot = int(sd.get("rotation", rot) or 0)
    if abs(rot) % 180 == 90:          # phone videos: ffmpeg auto-rotates, so swap dims
        w, h = h, w
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {
        "duration": float(d["format"]["duration"]),
        "has_audio": any(s["codec_type"] == "audio" for s in streams),
        "width": w, "height": h, "fps": fps if 1 <= fps <= 240 else 30.0,
    }


def open_frames(path, vf, ss=None, t=None):
    """Decode video to raw BGR frames on stdout. Uses the Mac's hardware decoder (4x faster on
    4K/AV1) and falls back to software decoding if the hardware path yields nothing."""
    def cmd(hw):
        c = [FFMPEG, "-v", "error"] + (["-hwaccel", "videotoolbox"] if hw else [])
        c += (["-ss", f"{ss:.3f}"] if ss is not None else []) + (["-t", f"{t:.3f}"] if t is not None else [])
        return c + ["-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    proc = subprocess.Popen(cmd(True), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    head = proc.stdout.read(4096)
    if head:
        return proc, head
    proc.kill()
    proc.wait()
    proc = subprocess.Popen(cmd(False), stdout=subprocess.PIPE)
    return proc, b""


def read_exact(proc, n, head):
    """Read one frame, consuming any bytes already peeked by open_frames."""
    if head:
        data = head + proc.stdout.read(n - len(head))
        return data, b""
    return proc.stdout.read(n), b""


_T0 = time.time()


def log(msg):
    print(f"• {msg}  [{time.time() - _T0:.0f}s]", flush=True)
