"""Content calendar + YouTube Shorts upload (your own Google OAuth client).

TikTok and Instagram stay copy-and-post: their posting APIs require Meta/TikTok to approve a
developer app first. The calendar still tracks those posts and reminds you when they're due.
"""
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import settings as cfg

CAL = cfg.ROOT / "calendar.json"
SECRETS = cfg.ROOT / "secrets"
YT_TOKEN = SECRETS / "youtube_token.json"
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
PLATFORMS = ("tiktok", "instagram", "youtube", "x", "facebook", "linkedin", "bluesky", "other")


def _load():
    return json.loads(CAL.read_text()) if CAL.exists() else []


def _save(items):
    CAL.write_text(json.dumps(items, indent=1, ensure_ascii=False))


def list_posts():
    return sorted(_load(), key=lambda p: p.get("when") or "")


def add_post(job, clip, platform, when, caption, title=""):
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform {platform}")
    datetime.fromisoformat(when)                       # validates
    items = _load()
    item = {"id": uuid.uuid4().hex[:10], "job": job, "clip": clip, "platform": platform, "when": when,
            "caption": caption, "title": title, "status": "planned", "url": "", "created": time.time()}
    items.append(item)
    _save(items)
    return item


def update_post(post_id, **fields):
    items = _load()
    for it in items:
        if it["id"] == post_id:
            for k in ("when", "caption", "title", "status", "url", "platform"):
                if k in fields and fields[k] is not None:
                    it[k] = fields[k]
            _save(items)
            return it
    raise KeyError(post_id)


def delete_post(post_id):
    _save([p for p in _load() if p["id"] != post_id])


def due(now=None):
    now = now or datetime.now()
    return [p for p in _load() if p["status"] == "planned" and datetime.fromisoformat(p["when"]) <= now]


# ---------- YouTube ----------

def youtube_status():
    secret = cfg.load().get("youtube_client_secret")
    return {"client_configured": bool(secret and Path(secret).exists()), "connected": YT_TOKEN.exists()}


def youtube_connect():
    """Opens Google's consent page in your browser (runs a one-off local callback server)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    secret = cfg.load().get("youtube_client_secret")
    if not secret or not Path(secret).exists():
        raise ValueError("Add your Google OAuth client JSON in Settings first")
    flow = InstalledAppFlow.from_client_secrets_file(secret, YT_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    SECRETS.mkdir(exist_ok=True)
    YT_TOKEN.write_text(creds.to_json())
    YT_TOKEN.chmod(0o600)
    return True


def _youtube():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not YT_TOKEN.exists():
        raise ValueError("YouTube isn't connected yet (Settings → Connect YouTube)")
    creds = Credentials.from_authorized_user_file(str(YT_TOKEN), YT_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YT_TOKEN.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def youtube_upload(mp4, title, description, tags=(), publish_at=None, privacy="public"):
    """Upload a Short. With publish_at (ISO, future) it's uploaded private and YouTube publishes it then."""
    from googleapiclient.http import MediaFileUpload
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        when = datetime.fromisoformat(publish_at)
        if when.tzinfo is None:
            when = when.astimezone()
        status = {"privacyStatus": "private", "publishAt": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "selfDeclaredMadeForKids": False}
    body = {"snippet": {"title": title[:100], "description": description[:4900], "tags": [t.lstrip("#") for t in tags][:15],
                        "categoryId": "22"}, "status": status}
    req = _youtube().videos().insert(part="snippet,status", body=body,
                                     media_body=MediaFileUpload(str(mp4), chunksize=-1, resumable=True, mimetype="video/mp4"))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return {"id": resp["id"], "url": f"https://youtube.com/shorts/{resp['id']}"}


def youtube_stats(video_ids):
    out = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        r = _youtube().videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            s = it["statistics"]
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out
