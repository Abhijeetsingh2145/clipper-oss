# Clipper

An OpusClip-style clipping studio that runs entirely on your own machine. You give it a long video and a campaign brief, and it gives you ready-to-post short clips. Each clip comes with captions, a post caption that follows the brief's rules, an honest quality score, and an editor to change anything.

## Runs locally only — there is no hosted version

This is not a SaaS app and there's nothing to sign up for. Everything — video processing, transcription, the AI model, and your files — runs on the machine you install it on. There is no server to deploy this to; see [Requirements](#requirements) for why.

- **Hardware:** a Mac with Apple Silicon (M1 or later) — **this is a hard requirement, not a current limitation that's planned to lift.** Transcription runs on [MLX](https://github.com/ml-explore/mlx), Apple's own machine-learning framework for its unified memory architecture; there is no MLX runtime for Windows or Linux, and the launcher scripts are `zsh`, which Windows doesn't have by default. Porting off MLX (e.g. to `faster-whisper`) and swapping the hardcoded macOS hardware video encoder for a cross-platform one would be a real code change, not a packaging fix.
- **ffmpeg** installed and on your `PATH` (`brew install ffmpeg`) — every render, reframe, and caption burn-in shells out to it.
- **[Ollama](https://ollama.com)** running locally with a model pulled (e.g. `ollama pull qwen3:8b`) for the AI picking/scoring/captioning — or an OpenAI-compatible cloud API key instead (see below).
- **Python 3.12+** and [uv](https://docs.astral.sh/uv/).
- **One instance per machine:** the web server always binds `127.0.0.1:8765` (not configurable yet), so you can't run two copies on the same Mac at once — starting a second one just fails to bind, it's not a broken install.

## Setup

```bash
git clone https://github.com/<you>/clipper.git
cd clipper
uv sync
./clip-web
```

Then open http://127.0.0.1:8765 — it binds to localhost only, so nothing outside this machine can reach it.

## API keys — how they're handled

Clipper can run **fully offline** with a local Ollama model and never needs a key. Keys are only needed for optional features, and you enter them yourself in the app's **Settings** page, never in a config file you'd commit:

| Key | What it's for | Required? |
|---|---|---|
| A cloud model key (`cloud_url` + `cloud_key`, OpenAI-compatible — Anthropic, OpenAI, Gemini, etc.) | Swap the local Ollama model for a hosted one | No — local Ollama works with no key at all |
| [Pexels](https://www.pexels.com/api/) key | B-roll search | No — only if you enable B-roll |
| Google OAuth client JSON | YouTube Shorts upload/scheduling | No — only if you use that feature |

**Where keys live:** everything you enter in Settings is written to `settings.json` in the project root, which this repo's `.gitignore` already excludes, and the app `chmod`s that file to `600` (owner-read/write only) every time it's saved — see `clipper/settings.py`. Never commit `settings.json`, and never paste a real key into an issue, PR, or screenshot. If a key is ever exposed, revoke and regenerate it with that provider immediately.

## What it does

**Clipping**
- Picks highlights from the transcript for podcasts, interviews and vlogs, and from loudness, motion and cut density for trailers, gameplay, music and sports.
- **AI Copilot:** tell it what to look for. **Topic search** finds where something is discussed, with a one-click clip.
- **Reprompt:** get a new set of clips without re-transcribing.
- **Timeframe:** clip only part of the video.
- Clip count and length are up to you (presets, a custom range, or long clips of 2–15 minutes).
- **Caption-only mode:** captions the whole video without clipping.

**Score (clip quality estimate, not a view prediction)**
- 7 measured parts, each with its evidence:
  - Hook: when speech starts, loudness of the first 3 s, and question/number/"you" checked in the text, plus the model's judgment.
  - Makes sense on its own: dangling openers, mid-sentence or setup endings, and the model's check.
  - Payoff: the model's quote must appear in the clip's ending, combined with the ending's structure.
  - Pacing: words per second, silence, longest pause.
  - Visual energy: motion percentile, cuts per 10 s, face on screen.
  - Audio dynamics: loudness variation.
  - Campaign fit: the brief's focus, banned words, length.
- Clips are ranked by the score.
- **Calibration:** once you log 20+ posted clips in Analytics, the weights are refit to your real views per follower. Analytics shows how well the score ranked your clips before and after.

**Reframing**
- Layouts:
  - Track: follows whoever's mouth is moving.
  - Split screen: two people.
  - Gameplay: game on top, facecam below.
  - Screen share: screen plus speaker.
  - Full frame on a blurred background, center crop, manual crop.
  - **Auto:** picks a layout per camera shot.
- Aspect ratios: 9:16, 1:1, 4:5, 16:9, or original.

**Captions**
- 7 templates: Bold Pop, Karaoke, Boxed, Minimal, Neon, One word, Subtitle.
- AI keyword highlights (numbers always), colour emoji, colours per speaker.
- Profanity censoring (asterisks plus a beep), with a custom word list.
- Timing offset, custom font upload, per-word text fixes.

**Audio**
- Speech enhancement, RNNoise denoising, two-pass loudness to −14 LUFS.
- Cut "um/uh", long pauses and repeated takes.
- Background music that ducks under speech.
- Sound effects: synthesized whoosh/pop/hit/riser plus your own.
- AI voice-over: Kokoro, 54 voices, several languages; it can write the script from the clip.

**Visuals and branding**
- Zooms on key words, punch-ins on jump cuts, sharpening.
- Logo (animated), image overlays, watermark, hook title, intro/outro.
- B-roll from your library folder, plus Pexels with your free key; the source of every insert is recorded.
- **Brand kits** save and reuse any of this.

**Editor**
- Player, a draggable timeline (trim, add sections, reorder), and a transcript (click to cut, highlight, add emoji, or fix text).
- Panels for captions, framing, audio, visuals, post and score.
- **AI Producer:** plain-English edits. The model can only choose from a fixed menu of edits, and every value is validated.
- Targeted regeneration: new keywords, emoji, title, hook or B-roll.
- Full version history with undo/redo and restore.
- Shortcuts: Space, J/K/L, I/O, ⌘S, ⌘Z.

**Campaign compliance**
- Reads the brief from a Google Doc, PDF or pasted text, and extracts every rule: count, length, captions, `#Ad` disclosure first, hashtag limit, required tags and points, banned words, language.
- Anything the brief bans (music, AI voices, outside footage, logos) is removed automatically with a warning.
- Every clip gets a checklist. The rules it can't enforce are listed for you to check by hand.

**Publishing and analytics**
- Content calendar with due reminders.
- YouTube Shorts upload and scheduling through your own Google OAuth client (setup steps are in Settings). TikTok and Instagram stay copy-and-post.
- Performance log, results by campaign, YouTube stats sync, score calibration.

**Import:** upload, a path on this Mac, or a link (YouTube, Drive, Twitch, X, Vimeo, Dropbox, direct file).

**For agents:** REST API (docs at http://127.0.0.1:8765/docs), and an MCP server:

```bash
claude mcp add clipper -- $(pwd)/clip-mcp
```

## Command line

```bash
./clip video.mp4 --rules brief.pdf --clips 3 --min 20 --max 40 --prompt "pricing talk" --aspect 9:16 --template karaoke
```

## Under the hood

- Whisper large-v3-turbo (MLX) for transcription, then a one-time per-video analysis: scene cuts, motion, YuNet faces with mouth activity, loudness. It's cached in `cache/videos/`.
- Qwen3 8B through Ollama, with structured output, for brief reading, picking, scoring judgments, captions and edits.
- Every clip is an editable spec (`jobs/<id>/out/clip_NN.json` plus its `.versions/`).
- One renderer turns a spec into an MP4: hardware decode, then a Python compositor, then VideoToolbox encode with libass captions.
- Tests: `PYTHONPATH=. uv run pytest tests`

## Honest limits

- **Not built:** team workspaces, enterprise processing, AI-generated video B-roll and AI super-resolution (too heavy for 16 GB). "Sharpen" is an unsharp mask, not AI upscaling.
- **TikTok/IG auto-posting:** needs those platforms to approve a developer app.
- **Active-speaker tracking** uses mouth movement. It can be fooled by people chewing or by heavy compression; you can pin who to follow in the editor.
- **Whisper** catches most "um"s but not all, and lyrics under loud music can be wrong.
- **Scores** are estimates until calibrated on your own results.
