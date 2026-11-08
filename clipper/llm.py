"""Text models: local Ollama, or any OpenAI-compatible gateway (OmniRoute, OpenRouter, LiteLLM...).

Model names starting with "cloud:" go to the gateway set in Settings; everything else goes to
Ollama. If a cloud call fails or returns unusable JSON, the same request is retried on the best
local model, so a job never stops because a provider is down.
"""
import json
import re
import threading
import time
from functools import lru_cache

import requests

from . import settings as cfg
from .common import log

OLLAMA = "http://localhost:11434"
RETRY_WAITS = (0, 8, 20, 40)      # rounds over the cloud models before falling back (gateways rate-limit in bursts)
BROKEN = set()                    # backups that returned malformed answers twice this session
BAD = {}                          # model -> malformed answers so far
STRIKES = {}                      # model -> rate-limit (429) replies in a row
COOLING = {}                      # model -> time until which it is skipped
COOL_DOWN = 15 * 60
CLOUD_DOWN = {"until": 0.0}       # when every cloud model failed: skip the cloud entirely until then
CLOUD_DOWN_FOR = 10 * 60
CLOUD_TIMEOUT = 60                # seconds per cloud request (a hung gateway must not stall a job)
CLOUD_BUDGET = 90                 # seconds for ALL cloud attempts of one call, retries included
LAST = {"model": None}            # which model produced the last answer
PREFERRED = ["qwen3:8b", "qwen3:14b", "hermes3:latest", "hermes3"]
CLOUD = "cloud:"


@lru_cache(maxsize=1)
def installed_models():
    try:
        return [m["name"] for m in requests.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])]
    except requests.RequestException:
        return []


def local_model():
    have = installed_models()
    return next((m for m in PREFERRED if m in have), have[0] if have else "qwen3:14b")


def default_model():
    """The model chosen in Settings (local or cloud), else the best installed local one."""
    return cfg.load().get("model") or local_model()


# ---------- cloud gateway (OpenAI-compatible) ----------
def _gateway():
    s = cfg.load()
    url = (s.get("cloud_url") or "").strip().rstrip("/")
    if url and not url.endswith("/v1") and "/v1/" not in url + "/":
        url += "/v1"
    return url, (s.get("cloud_key") or "").strip()


def _headers(key, url=""):
    h = {"Authorization": f"Bearer {key}"} if key else {}
    if "api.anthropic.com" in url:           # Anthropic's OpenAI-compatible endpoint still requires this
        h["anthropic-version"] = "2023-06-01"
    return h


SCAN_FILE = cfg.ROOT / "cache" / "cloud_models.json"
_scan_lock = threading.Lock()
SCAN = {"running": False, "done": 0, "total": 0}


def cloud_models(timeout=25):
    """Model ids the gateway offers ([] when not configured). Raises on a bad URL/key."""
    url, key = _gateway()
    if not url:
        return []
    r = requests.get(f"{url}/models", headers=_headers(key, url), timeout=timeout)
    if r.status_code in (401, 403):
        raise RuntimeError("The gateway rejected the API key")
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))


def scan_results():
    """Last check of every gateway model: {url, checked_at, models: {id: {ok, correct, seconds, error}}}."""
    try:
        data = json.loads(SCAN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if data.get("url") == _gateway()[0] else {}      # results belong to one gateway


def working_models():
    """Models that passed the check, fastest first."""
    rows = [(m, r) for m, r in scan_results().get("models", {}).items() if r.get("ok") and r.get("correct")]
    return [m for m, r in sorted(rows, key=lambda x: x[1]["seconds"])]


NOT_CHAT = re.compile(r"embed|rerank|whisper|transcri|tts|speech|audio|dall-?e|image|imagen|flux|stable-?diff|sdxl|"
                      r"midjourney|video|veo|sora|kling|moderation|guard|ocr|clip-vit|bge-|e5-", re.I)


STRONG = re.compile(r"claude|gpt-5|gpt-4\.1|gpt-4o|o3|o4|gemini-(2\.5|3)|deepseek|qwen3|kimi|glm|llama-4|grok|"
                    r"mistral-large|hermes|gemma-4|minimax", re.I)
MAX_PER_PROVIDER = 15


def _gateway_healthy(url, wait=180):
    """Wait until the gateway answers again (a hung upstream request can stall a small gateway)."""
    end = time.time() + wait
    while time.time() < end:
        try:
            requests.get(f"{url}/models", timeout=5)
            return True
        except requests.RequestException:
            time.sleep(10)
    return False


def scan_cloud(timeout=15):
    """Send gateway chat models one small structured request each, ONE AT A TIME (a small
    self-hosted gateway stalls under parallel load, and keeps waiting on hung providers after we
    give up). Probes 2 models per provider first and skips providers where both fail; then tests up
    to MAX_PER_PROVIDER models per working provider, strongest families first. After any timeout it
    pauses until the gateway is healthy again. Results are saved as they come in; a re-run resumes."""
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        url = _gateway()[0]
        SCAN.update(running=True, done=0, total=0, error="")
        try:
            ids = cloud_models(timeout=60)
        except Exception as e:
            SCAN["error"] = str(e)[:200]
            return
        prev = scan_results()
        results = {m: r for m, r in prev.get("models", {}).items() if m in ids and not r["error"].startswith(("provider ", "untested"))} \
            if prev.get("partial") else {}
        for m in ids:
            if NOT_CHAT.search(m):
                results[m] = {"ok": False, "correct": False, "seconds": None, "error": "not a text model"}

        def save(partial):
            SCAN_FILE.parent.mkdir(parents=True, exist_ok=True)
            SCAN_FILE.write_text(json.dumps({"url": url, "checked_at": time.time(), "partial": partial, "models": results}, indent=1))

        def run(models):
            for m in models:
                if m in results:
                    continue
                try:
                    r = test_cloud(m, timeout)
                    results[m] = {"ok": True, "correct": r["correct"], "seconds": r["seconds"], "error": ""}
                except Exception as e:
                    results[m] = {"ok": False, "correct": False, "seconds": None, "error": str(e)[:160]}
                    if isinstance(e, requests.Timeout):
                        _gateway_healthy(url)
                SCAN["done"] += 1
                save(True)

        provider = lambda m: m.split("/", 1)[0] if "/" in m else m
        groups = {}
        for m in ids:
            if m not in results or not results[m]["error"] == "not a text model":
                groups.setdefault(provider(m), []).append(m)
        for ms in groups.values():
            ms.sort(key=lambda m: 0 if STRONG.search(m) else 1)        # stable: strongest families first
        probes = [m for ms in groups.values() for m in ms[:2]]
        rest_all = [m for ms in groups.values() for m in ms[2:MAX_PER_PROVIDER]]
        SCAN.update(total=len([m for m in probes + rest_all]), done=len([m for m in probes + rest_all if m in results]))
        run(probes)
        rest = []
        for p, ms in groups.items():
            if any(results.get(m, {}).get("ok") for m in ms[:2]):
                rest += ms[2:MAX_PER_PROVIDER]
                for m in ms[MAX_PER_PROVIDER:]:
                    results.setdefault(m, {"ok": False, "correct": False, "seconds": None, "error": "untested (beyond the per-provider limit)"})
            else:
                for m in ms[2:]:
                    results.setdefault(m, {"ok": False, "correct": False, "seconds": None, "error": f"provider {p} not working"})
        SCAN["total"] = SCAN["done"] + len([m for m in rest if m not in results])
        run(rest)
        save(False)
    finally:
        SCAN["running"] = False
        _scan_lock.release()


def _extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _check(reply, schema):
    """Minimal schema check: an object with every required top-level field."""
    if schema.get("type") == "object":
        if not isinstance(reply, dict):
            raise ValueError("reply is not a JSON object")
        missing = [k for k in schema.get("required", []) if k not in reply]
        if missing:
            raise ValueError(f"reply is missing {missing}")
    return reply


def _ask_cloud(model, messages, schema, temperature, timeout=None):
    timeout = timeout or CLOUD_TIMEOUT
    url, key = _gateway()
    if not url:
        raise RuntimeError("No cloud gateway set in Settings")
    body = {"model": model, "messages": messages, "temperature": temperature, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}}
    r = requests.post(f"{url}/chat/completions", json=body, headers=_headers(key, url), timeout=timeout)
    if r.status_code in (400, 422):             # provider can't do schemas: ask for plain JSON, schema in the prompt
        hint = {"role": "system", "content": "Reply with only a JSON object matching this JSON schema:\n" + json.dumps(schema)}
        body = {**body, "messages": [hint, *messages], "response_format": {"type": "json_object"}}
        r = requests.post(f"{url}/chat/completions", json=body, headers=_headers(key, url), timeout=timeout)
        if r.status_code in (400, 422):
            body.pop("response_format")
            r = requests.post(f"{url}/chat/completions", json=body, headers=_headers(key, url), timeout=timeout)
    if r.status_code in (401, 403):
        raise RuntimeError("The gateway rejected the API key")
    r.raise_for_status()
    return _check(_extract_json(r.json()["choices"][0]["message"]["content"]), schema)


def _ask_local(model, messages, schema, temperature, num_ctx):
    body = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if model.startswith("qwen3"):
        body["think"] = False          # structured output only; thinking adds minutes per call
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=1200)
    except requests.ConnectionError:
        raise SystemExit("Ollama isn't running. Open the Ollama app (or run `ollama serve`) and try again.")
    if r.status_code == 404:
        raise SystemExit(f"Ollama model '{model}' isn't installed. Run: ollama pull {model}")
    r.raise_for_status()
    content = r.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError(f"Model returned invalid JSON: {content[:300]}")


def ask_json(prompt, schema, model=None, system=None, num_ctx=8192, temperature=0.2):
    """Ask the chosen model for a reply that matches a JSON schema."""
    model = model or default_model()
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    if model.startswith(CLOUD) and CLOUD_DOWN["until"] > time.time():
        model = local_model()                                   # gateway marked down: no waiting, straight to local
    if model.startswith(CLOUD):
        first = model[len(CLOUD):]
        # chosen model, then up to 2 other models that passed the check (gateways return "busy" often)
        # combos (no "provider/" prefix, e.g. OmniRoute's HERMES) route around busy providers themselves: try them first
        backups = sorted((x for x in working_models() if x != first and x not in BROKEN),
                         key=lambda x: (0 if "/" not in x else 1, 0 if STRONG.search(x) else 1))
        deadline = time.time() + CLOUD_BUDGET
        for wait in RETRY_WAITS:
            now = time.time()
            ready = [m for m in [first] + backups if m not in BROKEN and COOLING.get(m, 0) <= now][:3]
            if not ready or now + wait >= deadline:
                break                                           # nothing usable / out of time: fall back now
            if wait:
                log(f"Cloud models busy / rate-limited; retrying in {wait}s")
                time.sleep(wait)
            busy = False
            for m in ready:
                left = deadline - time.time()
                if left < 5:
                    break
                try:
                    out = _ask_cloud(m, messages, schema, temperature, timeout=min(CLOUD_TIMEOUT, left))
                    LAST["model"] = CLOUD + m
                    STRIKES.pop(m, None)
                    return out
                except requests.HTTPError as e:
                    code = getattr(e.response, "status_code", 0)
                    busy = busy or code in (408, 429, 500, 502, 503, 504)
                    if code == 429:                             # out of quota: rest it instead of retrying every call
                        STRIKES[m] = STRIKES.get(m, 0) + 1
                        if STRIKES[m] >= 2:
                            COOLING[m] = time.time() + COOL_DOWN
                            log(f"Cloud model {m} is rate-limited; skipping it for {COOL_DOWN // 60} minutes")
                            continue
                    log(f"Cloud model {m} failed ({code or str(e)[:80]})")
                except requests.RequestException as e:          # timeout / connection: the gateway is struggling
                    busy = True
                    log(f"Cloud model {m} failed ({str(e)[:80]})")
                except (RuntimeError, ValueError, KeyError, IndexError) as e:
                    BAD[m] = BAD.get(m, 0) + 1
                    if m != first and BAD[m] >= 2:              # repeatedly malformed: stop using it as a backup
                        BROKEN.add(m)
                    log(f"Cloud model {m} gave an unusable answer ({str(e)[:80]})")
            if not busy:
                break
        model = local_model()
        CLOUD_DOWN["until"] = time.time() + CLOUD_DOWN_FOR
        log(f"No cloud model answered: OmniRoute looks down or out of quota. Using the local {model} for the next "
            f"{CLOUD_DOWN_FOR // 60} minutes (faster, but weaker at picking and writing)")
    LAST["model"] = model
    return _ask_local(model, messages, schema, temperature, num_ctx)


def answered_by_fallback():
    """True when the last answer came from the local model although a cloud model is configured."""
    return (default_model() or "").startswith(CLOUD) and not (LAST["model"] or "").startswith(CLOUD)


def test_cloud(model, timeout=45):
    """Round-trip check for Settings: one tiny structured request, timed. Never falls back."""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "sum": {"type": "integer"}},
              "required": ["ok", "sum"]}
    t = time.time()
    reply = _ask_cloud(model, [{"role": "user", "content": "Return ok=true and sum=17+25 as JSON."}], schema, 0, timeout=timeout)
    return {"model": model, "reply": reply, "correct": reply.get("sum") == 42, "seconds": round(time.time() - t, 2)}
