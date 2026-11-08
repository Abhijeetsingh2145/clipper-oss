"""MCP server so AI agents (Claude Code, Claude Desktop…) can drive Clipper.

It talks to the running web app (http://127.0.0.1:8765), so start ~/clipper/clip-web first.
Register with Claude Code:
    claude mcp add clipper -- ~/clipper/clip-mcp
"""
import requests
from mcp.server.mcpserver import MCPServer

B = "http://127.0.0.1:8765"
mcp = MCPServer("clipper")


def _api(method, path, **kw):
    try:
        r = requests.request(method, B + path, timeout=600, **kw)
    except requests.ConnectionError:
        raise RuntimeError("Clipper isn't running. Start it with ~/clipper/clip-web")
    if not r.ok:
        raise RuntimeError(r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return r.json()


@mcp.tool()
def create_clips(video: str, brief_google_doc: str = "", brief_text: str = "", brief_path: str = "", prompt: str = "",
                 clips: int = 0, min_seconds: int = 0, max_seconds: int = 0, aspect: str = "9:16", template: str = "bold_pop",
                 caption_only: bool = False, start: float = 0, end: float = 0) -> dict:
    """Start a clipping job. `video` is a path on this Mac or a link (YouTube, Drive, Dropbox, direct file).
    Give the campaign brief as a Google Doc link, text or a file path. Returns the job id."""
    brief = {"url": brief_google_doc} if brief_google_doc else {"text": brief_text} if brief_text else {"path": brief_path} if brief_path else None
    settings = {"prompt": prompt, "aspect": aspect, "template": template, "captions": {"template": template}}
    for k, v in (("clips", clips), ("min", min_seconds), ("max", max_seconds), ("start", start), ("end", end)):
        if v:
            settings[k] = v
    if caption_only:
        settings["caption_only"] = True
    is_url = video.startswith(("http://", "https://"))
    return _api("POST", "/api/jobs", json={"video_url" if is_url else "video_path": video, "brief": brief, "settings": settings})


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Status, log tail, rules found in the brief and the clips (with scores, captions and checks)."""
    j = _api("GET", f"/api/jobs/{job_id}")
    return {"status": j["status"], "log": j["log"][-8:], "rules": j["rules"],
            "clips": [{k: c.get(k) for k in ("file", "seconds", "score", "title", "post_text", "warnings", "missing_points")}
                      for c in j["clips"]], "files": f"{B}/files/{job_id}/"}


@mcp.tool()
def list_jobs() -> list:
    """Recent clipping jobs."""
    return [{k: j.get(k) for k in ("id", "video", "status", "prompt")} for j in _api("GET", "/api/jobs")]


@mcp.tool()
def find_topic(job_id: str, query: str) -> list:
    """Find where a topic is discussed in the job's video (timestamps + text)."""
    return _api("GET", f"/api/jobs/{job_id}/search", params={"q": query})


@mcp.tool()
def add_clip(job_id: str, start: float, end: float) -> dict:
    """Make an extra clip from a chosen time range (seconds) of the job's video."""
    return _api("POST", f"/api/jobs/{job_id}/clips", json={"start": start, "end": end})


@mcp.tool()
def edit_clip(job_id: str, clip: str, instruction: str) -> dict:
    """Edit a clip in plain English (captions, trim, framing, zooms, audio clean-up, cut a phrase, better hook…).
    `clip` is like clip_01. Call render_clip afterwards to produce the video."""
    return _api("POST", f"/api/jobs/{job_id}/clips/{clip}/ai", json={"command": instruction})


@mcp.tool()
def render_clip(job_id: str, clip: str, preview: bool = False) -> dict:
    """Render a clip (full quality unless preview=True). Poll clip_render_status until done."""
    return _api("POST", f"/api/jobs/{job_id}/clips/{clip}/render", json={"preview": preview})


@mcp.tool()
def clip_render_status(job_id: str, clip: str) -> dict:
    """Render progress for a clip."""
    return _api("GET", f"/api/jobs/{job_id}/clips/{clip}/status")


@mcp.tool()
def reprompt(job_id: str, prompt: str, clips: int = 0) -> dict:
    """New set of clips from the same video and brief with different instructions."""
    return _api("POST", f"/api/jobs/{job_id}/reprompt", json={"prompt": prompt, **({"clips": clips} if clips else {})})


@mcp.tool()
def schedule_post(job_id: str, clip: str, platform: str, when_iso: str, caption: str) -> dict:
    """Add a post to the content calendar (platform: tiktok, instagram, youtube, x, facebook, linkedin, bluesky)."""
    return _api("POST", "/api/calendar", json={"job": job_id, "clip": clip, "platform": platform, "when": when_iso, "caption": caption})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
