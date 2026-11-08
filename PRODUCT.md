# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary user today: a single solo creator/operator running Clipper on their own Mac to turn long-form video into ready-to-post vertical clips across many niches (podcasts, gaming, trailers, vlogs, music, news). They move between campaigns fast — upload, set a brief, get clips, edit, publish — often juggling several projects in parallel. Confirmed: the product should anticipate a second user or shared/team context later, but nothing in today's design should assume multi-user yet (no auth, no roles).

## Product Purpose

An OpusClip-equivalent video-clipping tool that runs entirely on the user's own Mac: Whisper transcribes, a local or cloud LLM picks and scores clips against a campaign brief, faces/speakers are tracked for reframing, captions are burned in, B-roll/music/SFX are composited, and finished clips are ready to post (with a content calendar and direct YouTube upload). Success is clips that are genuinely well-picked and on-brief, not just technically rendered.

## Positioning

Nothing is invented or guessed and shown as fact. Every number the product shows is measured, not an AI vibe-check: the clip-quality score is a weighted sum of concretely measured sub-scores (hook timing from real loudness data, pacing from words-per-second, visual energy from real scene-cut counts), each one shown with its evidence (a quoted transcript line, a real loudness reading, a real file path). Competitor tools (Kixy, Opus, Everygen) show a single AI-judged number with no evidence; Clipper's score is inspectable and calibrates against the user's own real posted-clip results over time. This same honesty extends through the whole product: real ffmpeg output, real file paths, real job state — never a smoothed-over or fabricated status.

## Operating Context

- Runs locally via `~/clipper/clip-web` at `http://127.0.0.1:8765`, used in a normal desktop browser window, often alongside other local tools.
- A job: paste a video (upload, local path, or URL from YouTube/Twitch/X/Vimeo/Drive/direct), optionally paste or upload a campaign brief (Google Doc, PDF/file, pasted text, or none), pick what to clip (AI-picked highlights or captions-only), and generate. Clipping runs as a background job with live progress.
- A finished job yields several clips, each independently scored, editable (timeline trim, transcript text-editing to cut words, captions, layout/tracking, audio, B-roll/overlays, an AI Producer chat for natural-language edits, undo/redo), and exportable.
- Supporting surfaces: Projects (past jobs), Brand kits (fonts/colors/caption template/logo/intro-outro, reused across campaigns), Calendar (post scheduling + YouTube upload), Analytics (posted-clip results that calibrate the score), Settings (AI model/cloud gateway, YouTube connection, asset libraries), and a Caption remover tool (best-effort ffmpeg-based removal of burned-in captions from an existing clip, explicitly labeled as not true AI inpainting).
- The AI model is configurable: a local Ollama model (Qwen3) by default, or any OpenAI-compatible cloud gateway (OmniRoute, Anthropic, Gemini, etc.) the user supplies their own API key for. Video/transcription/rendering always stay on-device; only brief+transcript text goes to a configured cloud model, and jobs fall back to local if the cloud model fails.

## Capabilities and Constraints

- Single-user, no accounts/auth/billing today (by design, per current scope) — but the user has said design should leave room for a second user or team context later.
- No heavy generative video (AI-generated B-roll video, voice cloning, text-to-video "viral format" studios like competitors' Kixy Studio/Everygen Shorts Studio) — explicitly out of scope; the product clips real source footage, it doesn't generate fake footage.
- Upscaling is Lanczos + sharpening, explicitly labeled as that, never claimed as "AI upscaling."
- Caption remover is explicitly labeled "best effort," not true AI inpainting (ffmpeg delogo-based).
- Runs on a 16GB RAM Mac — local model and rendering choices are constrained by that.
- Terminology: "job" (one video + brief → clip-picking run), "clip" (one output), "spec" (the editable per-clip JSON a render is built from), "score"/"score parts" (the evidence-backed quality breakdown), "brief" (campaign rules a clip must follow).

## Brand Commitments

None fixed. The current name "Clipper" and a scissors mark exist in the codebase but the user has given full creative freedom to replace the visual identity; only the functional product (jobs, clips, scores, the workflow above) must be preserved.

## Evidence on Hand

- Real score breakdowns per clip (hook/self-contained/payoff/pacing/visual-energy/audio-dynamics/campaign-fit, each with its measured evidence) are live product data, not placeholder content — the redesign should be built to display real evidence prominently, not hide it behind a single number.
- Real job/clip data exists in `~/clipper/jobs` from actual campaigns run during development (gameplay trailers, a game character sizzle reel). No testimonials, press, or customer logos exist or should be fabricated — this is a personal tool, not a marketed product, so none of that applies.

## Product Principles

1. Never invent or smooth over data — show the real evidence behind every claim the UI makes (scores, job status, errors), even when that evidence is mundane (raw ffmpeg output, a file path).
2. Fast context-switching between parallel campaigns matters more than any single showcase moment — this is an Operate surface, not a marketing surface.
3. Honesty about current limits (best-effort caption removal, non-AI upscaling, local-vs-cloud model fallback) is part of the product's credibility, not something to visually downplay.
4. Design for one user today without assuming it stays that way — avoid decisions that would need to be undone for a second user or shared context later.
