"""Import a video from a link: YouTube/Twitch/X/Vimeo/Facebook/TikTok/Drive (via yt-dlp), Dropbox,
or any direct file URL."""
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


def _safe_name(s):
    return re.sub(r"[^\w.-]+", "_", s).strip("_")[:90] or "video"


def import_url(url, dest_dir, log=print):
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("Only http(s) links can be imported")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    host = u.netloc.lower()
    if "dropbox.com" in host:
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url) if "dl=0" in url else url + ("&" if "?" in url else "?") + "dl=1"
        return _direct(url, dest_dir, log)
    if u.path.lower().endswith(VIDEO_EXT):
        return _direct(url, dest_dir, log)
    return _ytdlp(url, dest_dir, log)


def _direct(url, dest_dir, log):
    name = _safe_name(Path(urlparse(url).path).name or "video.mp4")
    if not name.lower().endswith(VIDEO_EXT):
        name += ".mp4"
    dest = dest_dir / name
    log(f"Downloading {name}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            raise ValueError("That link points to a web page, not a video file")
        with open(dest, "wb") as f:
            for chunk in r.iter_content(4 << 20):
                f.write(chunk)
    return dest


def _ytdlp(url, dest_dir, log):
    exe = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
    log("Downloading with yt-dlp")
    out_tmpl = str(dest_dir / "%(title).80B [%(id)s].%(ext)s")
    proc = subprocess.run(
        [exe, "--no-playlist", "--restrict-filenames", "-f", "bv*[height<=2160]+ba/b", "--merge-output-format", "mp4",
         "--ffmpeg-location", "/opt/homebrew/opt/ffmpeg-full/bin", "-o", out_tmpl, "--print", "after_move:filepath", url],
        capture_output=True, text=True)
    if proc.returncode:
        err = (proc.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise ValueError(f"Couldn't download that link: {err}")
    path = Path(proc.stdout.strip().splitlines()[-1])
    if not path.exists():
        raise ValueError("Download finished but the file wasn't found")
    return path
