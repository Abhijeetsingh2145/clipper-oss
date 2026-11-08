"""Clipper local web app. Run: ~/clipper/clip-web  →  http://127.0.0.1:8765 (this Mac only)."""
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .frames import STYLES as FRAME_STYLES
from .modes import MODES
from . import analytics, publish
from . import captionremove as capremove
from . import settings as cfg
from . import spec as S
from .captions import TEMPLATES

ROOT = cfg.ROOT
JOBS = ROOT / "jobs"
STATIC = Path(__file__).parent / "static"
UPLOADS = ROOT / "uploads"
for d in (JOBS, UPLOADS):
    d.mkdir(exist_ok=True)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
UPLOAD_KINDS = {   # kind -> (folder, allowed extensions)
    "video": (UPLOADS, VIDEO_EXT),
    "brief": (UPLOADS, {".pdf", ".txt", ".md"}),
    "font": (cfg.FONTS, {".ttf", ".otf", ".ttc"}),
    "logo": (cfg.LOGOS, {".png", ".jpg", ".jpeg", ".webp"}),
    "music": (cfg.MUSIC_LIBRARY, {".mp3", ".wav", ".m4a", ".aac"}),
    "broll": (cfg.BROLL_LIBRARY, VIDEO_EXT | {".jpg", ".jpeg", ".png", ".webp"}),
    "intro": (ROOT / "assets" / "intros", VIDEO_EXT),
    "sfx": (ROOT / "assets" / "sfx", {".wav", ".mp3", ".m4a"}),
    "youtube_secret": (ROOT / "secrets", {".json"}),
}
GDOC = re.compile(r"^https://docs\.google\.com/document/d/([A-Za-z0-9_-]+)")
NAME_RE = re.compile(r"clip_\d{2,3}")

app = FastAPI(title="Clipper", docs_url="/docs")
procs = {}                          # job id -> Popen
renders = {}                        # (job, clip) -> status dict
render_q = queue.Queue()
lock = threading.Lock()


# ---------------- helpers ----------------
def job_dir(job_id):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
        raise HTTPException(404, "Unknown job")
    d = JOBS / job_id
    if not d.is_dir():
        raise HTTPException(404, "Unknown job")
    return d


def clip_path(job_id, name):
    if not NAME_RE.fullmatch(name):
        raise HTTPException(404, "Unknown clip")
    p = job_dir(job_id) / "out" / f"{name}.json"
    if not p.exists():
        raise HTTPException(404, "Unknown clip")
    return p


def meta(d):
    return json.loads((d / "job.json").read_text())


def save_meta(d, m):
    (d / "job.json").write_text(json.dumps(m, indent=1))


def py(*args):
    return [sys.executable, "-u", "-m", *args]


def _clean_log(text):
    out = []
    for line in text.replace("\r", "\n").splitlines():
        if line.strip() and not re.search(r"\d+%\||it/s|frames/s", line):
            out.append(line)
    return out[-200:]


def _busy():
    return any(p.poll() is None for p in procs.values())


# ---------------- pages ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/static/{name}")
def static(name: str):
    if not re.fullmatch(r"[\w-]+\.(js|css|svg)", name):
        raise HTTPException(404)
    return FileResponse(STATIC / name)


@app.get("/static/frames/{name}")
def frame_thumb(name: str):
    """Thumbnails for the reel-frame picker."""
    if not re.fullmatch(r"[\w-]+\.jpg", name) or not (STATIC / "frames" / name).exists():
        raise HTTPException(404)
    return FileResponse(STATIC / "frames" / name)


# ---------------- meta / settings ----------------
@app.get("/api/meta")
def get_meta():
    from .audio import list_voices
    from .llm import SCAN, installed_models, scan_results, working_models
    from .sfx import library
    installed_models.cache_clear()
    scan = scan_results()
    return {
        "cloud_models": working_models(), "cloud_scan": {**SCAN, "checked_at": scan.get("checked_at"),
                                                         "tested": len(scan.get("models", {}))},
        "templates": {k: v["label"] for k, v in TEMPLATES.items()}, "frames": FRAME_STYLES,
        "modes": {k: {"label": v["label"], "hint": v["hint"]} for k, v in MODES.items()},
        "layouts": list(S.LAYOUTS), "aspects": list(S.ASPECTS) + ["original"],
        "voices": list_voices(), "sfx": sorted(library()), "models": installed_models(),
        "brands": cfg.list_brands(), "settings": cfg.public(), "youtube": publish.youtube_status(),
        "assets": {k: _list_assets(k) for k in ("font", "logo", "music", "broll", "intro", "sfx")},
        "busy": _busy(),
    }


def _list_assets(kind):
    folder, exts = UPLOAD_KINDS[kind]
    if not folder.exists():
        return []
    return [{"name": p.name, "path": str(p)} for p in sorted(folder.rglob("*")) if p.suffix.lower() in exts][:300]


@app.get("/api/settings")
def get_settings():
    return cfg.public()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    for k in cfg.SECRET_KEYS:
        if k in body and body[k] in (True, False):   # browser echoes the masked flag
            body.pop(k)
    cfg.save(body)
    return cfg.public()


@app.post("/api/settings/cloud/scan")
def scan_cloud_models():
    """Check every gateway model in the background; Settings then lists only the ones that work."""
    from .llm import SCAN, scan_cloud
    if not SCAN["running"]:
        threading.Thread(target=scan_cloud, daemon=True).start()
    return {"started": True}


@app.get("/api/settings/cloud/scan")
def scan_cloud_status():
    from .llm import SCAN, scan_results
    return {**SCAN, **scan_results()}


@app.post("/api/settings/cloud/test")
def test_cloud_model(body: dict = Body(...)):
    """Send one tiny structured request to a gateway model; reports speed and whether it answered correctly."""
    from .llm import CLOUD, test_cloud
    model = str(body.get("model") or "").removeprefix(CLOUD)
    if not model:
        raise HTTPException(400, "Pick a cloud model to test")
    try:
        return test_cloud(model)
    except Exception as e:
        raise HTTPException(502, f"{model}: {str(e)[:300]}")


@app.post("/api/settings/youtube/connect")
def youtube_connect():
    try:
        publish.youtube_connect()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return publish.youtube_status()


@app.post("/api/upload/{kind}")
async def upload(kind: str, file: UploadFile = File(...)):
    if kind not in UPLOAD_KINDS:
        raise HTTPException(404)
    folder, exts = UPLOAD_KINDS[kind]
    ext = Path(file.filename or "").suffix.lower()
    if ext not in exts:
        raise HTTPException(400, f"{kind} files must be one of: {', '.join(sorted(exts))}")
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w. -]+", "_", Path(file.filename).stem)[:80] + ext
    dest = folder / (f"{uuid.uuid4().hex[:6]}_{safe}" if kind in ("video", "brief") else safe)
    with open(dest, "wb") as f:
        while chunk := await file.read(8 << 20):
            f.write(chunk)
    if kind == "youtube_secret":
        dest.chmod(0o600)
        cfg.save({"youtube_client_secret": str(dest)})
    return {"path": str(dest), "name": dest.name}


# ---------------- caption remover (best effort -- see captionremove.py) ----------------
@app.post("/api/tools/caption-remove/band")
def caption_remove_band(body: dict = Body(...)):
    """Auto-guesses the caption band for an already-uploaded video."""
    p = Path(body.get("path", ""))
    if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Upload a video first (kind=video)")
    return capremove.guess_band(p)


@app.post("/api/tools/caption-remove")
def caption_remove(body: dict = Body(...)):
    """body: {path, band: {x,y,w,h}}. Writes a cleaned copy next to the upload and returns it."""
    p = Path(body.get("path", ""))
    if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Upload a video first (kind=video)")
    band = body.get("band") or capremove.guess_band(p)
    for k in ("x", "y", "w", "h"):
        if not isinstance(band.get(k), (int, float)) or band[k] < 0:
            raise HTTPException(400, "Bad caption band")
    out = p.with_name(p.stem + "_clean.mp4")
    try:
        capremove.remove(p, out, band)
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f"ffmpeg failed: {e.stderr[-400:] if e.stderr else e}")
    return {"path": str(out), "name": out.name, "band": band}


# ---------------- brands ----------------
@app.get("/api/brands")
def brands():
    return cfg.list_brands()


@app.post("/api/brands")
def save_brand(kit: dict = Body(...)):
    if not str(kit.get("name", "")).strip():
        raise HTTPException(400, "Give the brand kit a name")
    return cfg.save_brand(kit)


@app.delete("/api/brands/{name}")
def delete_brand(name: str):
    cfg.delete_brand(name)
    return {"ok": True}


# ---------------- jobs ----------------
def _resolve_brief(brief, d):
    """brief: {"url"} Google Doc | {"text"} | {"path"} uploaded file. Returns a file path or None."""
    if not brief:
        return None
    if brief.get("url"):
        m = GDOC.match(brief["url"].strip())
        if not m:
            raise HTTPException(400, "Only Google Docs links (docs.google.com/document/...) are supported")
        r = requests.get(f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt", timeout=30)
        if r.status_code != 200 or "text/plain" not in r.headers.get("content-type", ""):
            raise HTTPException(400, "Couldn't read that Google Doc. Share it as 'Anyone with the link'.")
        (d / "brief.txt").write_text(r.text)
        return d / "brief.txt"
    if brief.get("text", "").strip():
        (d / "brief.txt").write_text(brief["text"])
        return d / "brief.txt"
    if brief.get("path"):
        p = Path(brief["path"])
        if not p.is_file() or p.suffix.lower() not in (".pdf", ".txt", ".md"):
            raise HTTPException(400, "Brief file not found")
        dest = d / f"brief{p.suffix.lower()}"
        shutil.copy(p, dest)
        return dest
    return None


@app.post("/api/jobs")
def create_job(body: dict = Body(...)):
    with lock:
        if _busy():
            raise HTTPException(409, "A job is already running. Wait for it to finish or cancel it.")
        video_path, video_url = (body.get("video_path") or "").strip().strip("'\""), (body.get("video_url") or "").strip()
        if not video_path and not video_url:
            raise HTTPException(400, "Add a video (upload, path on this Mac, or a link)")
        if video_path:
            p = Path(video_path).expanduser()
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
                raise HTTPException(400, "Video path not found or not a video file")
        job_id = uuid.uuid4().hex[:12]
        d = JOBS / job_id
        d.mkdir()
        try:
            brief = _resolve_brief(body.get("brief"), d)
        except HTTPException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        settings = body.get("settings") or {}
        (d / "settings.json").write_text(json.dumps(settings))
        name = Path(video_path).name if video_path else video_url
        save_meta(d, {"id": job_id, "video": name, "created": time.time(), "status": "running",
                      "parent": body.get("parent"), "prompt": settings.get("prompt", ""), "brief": bool(brief)})
        threading.Thread(target=_run_job, args=(job_id, video_path, video_url, brief), daemon=True).start()
        procs[job_id] = _Pending()
        return {"id": job_id}


class _Pending:
    """Placeholder while a link is downloading, so the job counts as busy."""
    def poll(self):
        return None

    def terminate(self):
        self.cancelled = True


def _run_job(job_id, video_path, video_url, brief):
    d = JOBS / job_id
    log = open(d / "log.txt", "a")
    try:
        if video_url:
            from .importer import import_url
            log.write(f"• Importing {video_url}\n")
            log.flush()
            video_path = str(import_url(video_url, ROOT / "imports", lambda m: (log.write(f"• {m}\n"), log.flush())))
            m = meta(d)
            m["video"] = Path(video_path).name
            save_meta(d, m)
            if getattr(procs.get(job_id), "cancelled", False):
                raise RuntimeError("Cancelled")
        cmd = py("clipper", str(video_path), "--out", str(d / "out"), "--settings", str(d / "settings.json"))
        if brief:
            cmd += ["--rules", str(brief)]
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        procs[job_id] = p
        code = p.wait()
        status = "done" if code == 0 else "failed"
    except Exception as e:
        log.write(f"Error: {e}\n")
        status = "failed"
    log.close()
    m = meta(d)
    m["status"] = status
    save_meta(d, m)


@app.get("/api/jobs")
def list_jobs():
    out = []
    for d in sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (d / "job.json").exists():
            out.append(meta(d))
    return out[:50]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    d = job_dir(job_id)
    m = meta(d)
    out = d / "out"
    m["log"] = _clean_log((d / "log.txt").read_text(errors="replace")) if (d / "log.txt").exists() else []
    m["clips"] = json.loads((out / "clips.json").read_text()) if (out / "clips.json").exists() else []
    m["rules"] = json.loads((out / "rules.json").read_text()) if (out / "rules.json").exists() else None
    m["settings"] = json.loads((d / "settings.json").read_text()) if (d / "settings.json").exists() else {}
    for c in m["clips"]:
        c["render"] = renders.get((job_id, c["file"].replace(".mp4", "")))
    return m


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job_dir(job_id)
    p = procs.get(job_id)
    if p and p.poll() is None:
        p.terminate()
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    d = job_dir(job_id)
    p = procs.get(job_id)
    if p and p.poll() is None:
        raise HTTPException(409, "Cancel the job before deleting it")
    shutil.rmtree(d)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/reprompt")
def reprompt(job_id: str, body: dict = Body(...)):
    """New set of clips from the same video + brief with different instructions (transcript is reused)."""
    d = job_dir(job_id)
    ctx = json.loads((d / "out" / "job_context.json").read_text())
    settings = json.loads((d / "settings.json").read_text()) if (d / "settings.json").exists() else {}
    for k in ("prompt", "clips", "min", "max", "start", "end", "mode"):
        if k in body:
            settings[k] = body[k]
    if "mode" in body:                       # a content type replaces the old edit-style switch
        settings.pop("edit_style", None)
        if not body["mode"]:
            settings.pop("mode", None)
    brief = next(iter(sorted(d.glob("brief.*"))), None)
    return create_job({"video_path": ctx["video"], "brief": {"path": str(brief)} if brief else None,
                       "settings": settings, "parent": job_id})


@app.get("/api/jobs/{job_id}/transcript")
def transcript(job_id: str, roman: bool = False):
    """The video's transcript; roman=1 adds Hinglish spellings (made once, then cached)."""
    ctx = json.loads((job_dir(job_id) / "out" / "job_context.json").read_text())
    path = Path(ctx["cache"]) / "transcript.json"
    tr = json.loads(path.read_text())
    if roman:
        from .translit import needs_roman, roman_transcript
        if needs_roman(tr):
            tr = roman_transcript(tr, path, ctx.get("model"))
    return tr


@app.get("/api/jobs/{job_id}/search")
def search(job_id: str, q: str):
    from .picker import topic_search
    if not q.strip():
        return []
    d = job_dir(job_id)
    ctx = json.loads((d / "out" / "job_context.json").read_text())
    tr = json.loads((Path(ctx["cache"]) / "transcript.json").read_text())
    return topic_search(tr, q.strip()[:200], ctx.get("model"))


@app.post("/api/jobs/{job_id}/clips")
def add_clip(job_id: str, body: dict = Body(...)):
    d = job_dir(job_id)
    s, e = float(body["start"]), float(body["end"])
    if e - s < 1:
        raise HTTPException(400, "A clip must be at least 1 second long")
    key = (job_id, f"new_{int(s)}")
    renders[key] = {"state": "rendering", "started": time.time()}

    def work():
        r = subprocess.run(py("clipper.tasks", "add", str(d / "out"), str(s), str(e)), cwd=ROOT, capture_output=True, text=True)
        renders[key] = {"state": "done" if r.returncode == 0 else "failed", "error": r.stderr[-400:]}
    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


# ---------------- clips (editor) ----------------
@app.get("/api/jobs/{job_id}/clips/{name}")
def get_clip(job_id: str, name: str):
    p = clip_path(job_id, name)
    ctx = json.loads((p.parent / "job_context.json").read_text())
    return {"spec": S.load(p), "versions": S.versions(p), "render": renders.get((job_id, name)),
            "has_preview": (p.parent / f"{name}.preview.mp4").exists(), "source_duration": ctx["info"]["duration"]}


@app.put("/api/jobs/{job_id}/clips/{name}")
def put_clip(job_id: str, name: str, body: dict = Body(...)):
    p = clip_path(job_id, name)
    spec = S.normalize(body["spec"])
    if spec["source"] != S.load(p)["source"]:
        raise HTTPException(400, "The source video can't be changed")
    from .editor import recompute_cuts
    recompute_cuts(spec)
    v = S.save(p, spec, (body.get("action") or "edited")[:200], "user")
    return {"v": v, "duration": round(S.duration(spec), 2)}


def _render_worker():
    while True:
        job_id, name, preview = render_q.get()
        p = JOBS / job_id / "out" / f"{name}.json"
        renders[(job_id, name)] = {"state": "rendering", "preview": preview, "started": time.time()}
        r = subprocess.run(py("clipper.tasks", "render", str(p)) + (["--preview"] if preview else []), cwd=ROOT,
                           capture_output=True, text=True)
        renders[(job_id, name)] = {"state": "done" if r.returncode == 0 else "failed", "preview": preview,
                                   "finished": time.time(), "error": r.stderr[-600:] if r.returncode else ""}


threading.Thread(target=_render_worker, daemon=True).start()


@app.post("/api/jobs/{job_id}/clips/{name}/render")
def render_clip(job_id: str, name: str, body: dict = Body(default={})):
    clip_path(job_id, name)
    preview = bool(body.get("preview", True))
    renders[(job_id, name)] = {"state": "queued", "preview": preview}
    render_q.put((job_id, name, preview))
    return renders[(job_id, name)]


@app.get("/api/jobs/{job_id}/clips/{name}/status")
def clip_status(job_id: str, name: str):
    clip_path(job_id, name)
    return renders.get((job_id, name)) or {"state": "idle"}


def _ctx(job_id):
    from .pipeline import load_context
    out = job_dir(job_id) / "out"
    job, info, tr, an = load_context(out)
    rules = json.loads((out / "rules.json").read_text())
    rules["_text"] = job.get("rules_text", "")
    return job, an, rules


@app.post("/api/jobs/{job_id}/clips/{name}/ai")
def ai_edit(job_id: str, name: str, body: dict = Body(...)):
    from .editor import apply_ops, plan
    from .pipeline import enforce_brief
    p = clip_path(job_id, name)
    command = str(body.get("command", "")).strip()[:500]
    if not command:
        raise HTTPException(400, "Type what you want changed")
    job, an, rules = _ctx(job_id)
    spec = S.load(p)
    proposal = plan(command, spec, job.get("model"))
    new, done = apply_ops(spec, proposal["ops"], an, rules, job.get("model"))
    warnings = enforce_brief(new, rules)
    if done:
        S.save(p, new, f"AI: {command} → {'; '.join(done)}"[:300], "ai")
    return {"done": done, "explanation": proposal.get("explanation", ""), "warnings": warnings, "changed": bool(done)}


@app.post("/api/jobs/{job_id}/clips/{name}/regenerate")
def regen(job_id: str, name: str, body: dict = Body(...)):
    from .editor import regenerate
    p = clip_path(job_id, name)
    job, an, rules = _ctx(job_id)
    spec = S.load(p)
    msg = regenerate(spec, str(body.get("target")), an, rules, job.get("model"))
    S.save(p, spec, f"Regenerated {body.get('target')}: {msg}"[:300], "ai")
    return {"message": msg}


@app.post("/api/jobs/{job_id}/clips/{name}/restore")
def restore(job_id: str, name: str, body: dict = Body(...)):
    p = clip_path(job_id, name)
    S.restore(p, int(body["v"]))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/clips/{name}/rescore")
def rescore(job_id: str, name: str):
    p = clip_path(job_id, name)
    r = subprocess.run(py("clipper.tasks", "rescore", str(p)), cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise HTTPException(500, r.stderr[-300:])
    return S.load(p)["meta"].get("score_parts")


@app.post("/api/jobs/{job_id}/clips/{name}/voice-script")
def voice_script(job_id: str, name: str, body: dict = Body(default={})):
    from .llm import ask_json
    p = clip_path(job_id, name)
    spec = S.load(p)
    job, an, rules = _ctx(job_id)
    text = " ".join(w["w"] for w in S.output_words(spec))
    secs = S.duration(spec)
    reply = ask_json(
        f"Write a voice-over narration for a {secs:.0f}-second short video, about {int(secs * 2.4)} words, "
        f"in a {body.get('tone') or 'natural, energetic'} tone. Only use facts from the clip. Clip content: {text[:2500]}",
        {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}, job.get("model"), temperature=0.6)
    return {"script": reply["script"].strip()}


@app.get("/files/{job_id}/{name}")
def get_file(job_id: str, name: str):
    out = job_dir(job_id) / "out"
    if not re.fullmatch(r"[\w.-]+\.(mp4|txt|md|json|png)", name):
        raise HTTPException(404)
    f = out / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.get("/media")
def media(path: str):
    """Serve a file from Clipper's own asset folders (for previews in the UI)."""
    p = Path(path).resolve()
    allowed = [cfg.ROOT / "assets", cfg.ROOT / "cache" / "broll", cfg.ROOT / "uploads"]
    if not any(str(p).startswith(str(a.resolve()) + "/") for a in allowed) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------------- calendar / publishing ----------------
@app.get("/api/calendar")
def calendar():
    return {"posts": publish.list_posts(), "due": publish.due(), "youtube": publish.youtube_status()}


@app.post("/api/calendar")
def calendar_add(body: dict = Body(...)):
    try:
        return publish.add_post(body["job"], body["clip"], body["platform"], body["when"], body.get("caption", ""), body.get("title", ""))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Invalid post: {e}")


@app.put("/api/calendar/{post_id}")
def calendar_update(post_id: str, body: dict = Body(...)):
    try:
        return publish.update_post(post_id, **body)
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/calendar/{post_id}")
def calendar_delete(post_id: str):
    publish.delete_post(post_id)
    return {"ok": True}


@app.post("/api/calendar/{post_id}/youtube")
def calendar_youtube(post_id: str, body: dict = Body(default={})):
    post = next((p for p in publish.list_posts() if p["id"] == post_id), None)
    if not post:
        raise HTTPException(404)
    mp4 = job_dir(post["job"]) / "out" / f"{post['clip']}.mp4"
    spec = S.load(job_dir(post["job"]) / "out" / f"{post['clip']}.json")
    tags = spec["meta"].get("post", {}).get("hashtags", [])
    try:
        res = publish.youtube_upload(mp4, post.get("title") or spec["title"].get("text") or "Short", post["caption"], tags,
                                     publish_at=post["when"] if body.get("schedule", True) else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    publish.update_post(post_id, status="uploaded", url=res["url"])
    analytics.record(post["job"], post["clip"], "youtube", res["url"], spec["meta"].get("score_parts"))
    return res


# ---------------- analytics ----------------
@app.get("/api/analytics")
def get_analytics():
    return analytics.summary()


@app.post("/api/analytics")
def add_analytics(body: dict = Body(...)):
    score_parts = None
    try:
        score_parts = S.load(clip_path(body["job"], body["clip"]))["meta"].get("score_parts")
    except (HTTPException, KeyError):
        pass
    it = analytics.record(body.get("job", ""), body.get("clip", ""), body.get("platform", "other"), body.get("url", ""),
                          score_parts, body.get("campaign", ""), body.get("niche", ""), body.get("followers"))
    return analytics.update(it["id"], **{k: body.get(k) for k in ("views", "likes", "comments", "shares")})


@app.put("/api/analytics/{item_id}")
def put_analytics(item_id: str, body: dict = Body(...)):
    try:
        return analytics.update(item_id, **body)
    except KeyError:
        raise HTTPException(404)


@app.delete("/api/analytics/{item_id}")
def del_analytics(item_id: str):
    analytics.delete(item_id)
    return {"ok": True}


@app.post("/api/analytics/calibrate")
def calibrate(body: dict = Body(default={})):
    return analytics.calibrate(dry_run=bool(body.get("dry_run")))


@app.post("/api/analytics/sync-youtube")
def sync_youtube():
    try:
        return {"updated": analytics.sync_youtube()}
    except ValueError as e:
        raise HTTPException(400, str(e))


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
