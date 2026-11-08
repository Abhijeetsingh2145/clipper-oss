import json
from pathlib import Path

from .common import log

DEFAULT_WHISPER = "mlx-community/whisper-large-v3-turbo"

# Phrases Whisper invents over music, tones or silence.
GHOST_PHRASES = {"thank you", "thank you.", "thanks for watching", "thanks for watching!", "you", "bye", "bye.",
                 "please subscribe", "subscribe to my channel", "music", "[music]", "(music)"}


FILLER_PROMPT = "Umm, let me think like, hmm... Okay, uh, here's what I'm, like, thinking."


def _free_whisper():
    """Release Whisper's ~2 GB before the 14B language model runs (16 GB Macs otherwise swap)."""
    try:
        import gc

        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder
        ModelHolder.model, ModelHolder.model_path = None, None
        gc.collect()
        mx.clear_cache()
    except Exception:
        pass


LATIN_LANGS ={"en", "es", "fr", "de", "pt", "it", "nl"}


def _mostly_latin(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() or "À" <= c <= "ɏ" for c in letters) / len(letters) > 0.8


def _hallucinated(seg, text):
    # mlx-whisper's no_speech_prob stays ~0 on tones/music, so word confidence is the useful signal.
    if not any(ch.isalnum() for ch in text):
        return True
    probs = [w.get("probability", 1.0) for w in seg.get("words", [])] or [1.0]
    if seg.get("compression_ratio", 0) > 2.6:      # same line repeated over and over
        return True
    if sum(probs) / len(probs) < 0.35:
        return True
    return text.lower().strip(" .!") in {p.strip(" .!") for p in GHOST_PHRASES} and min(probs) < 0.6


def detect_language(video, model=DEFAULT_WHISPER, duration=None):
    """Spoken language from a 30 s sample in the middle of the video, with NO prompt (an English
    prompt makes Whisper 'hear' English and translate Hindi, Spanish... into broken English)."""
    import subprocess
    import tempfile

    import mlx_whisper

    from .common import FFMPEG
    start = max(0.0, (duration or 60) / 2 - 15)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "sample.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{start:.1f}", "-t", "30", "-i", str(video), "-vn", "-ac", "1",
                        "-ar", "16000", str(wav)], check=True)
        res = mlx_whisper.transcribe(str(wav), path_or_hf_repo=model, condition_on_previous_text=False)
    return res.get("language") or "en"


def transcribe(video, cache_path, has_audio, model=DEFAULT_WHISPER, language=None, duration=None):
    cache = Path(cache_path)
    if cache.exists():
        saved = json.loads(cache.read_text())
        # transcripts made before language detection were forced through an English prompt
        if (language and saved.get("language") == language) or (not language and saved.get("detected")):
            log("Using saved transcript")
            return saved
    if not has_audio:
        tr = {"language": None, "segments": [], "words": [], "detected": True}
    else:
        import mlx_whisper
        if not language:
            language = detect_language(video, model, duration)
            log(f"Spoken language detected: {language}")
        log("Transcribing with Whisper (first run downloads the model, ~1.6 GB)")
        res = mlx_whisper.transcribe(
            str(video), path_or_hf_repo=model, word_timestamps=True,
            condition_on_previous_text=False,  # stops repeated-line hallucinations on music
            language=language or None,         # auto-detect guesses wildly on music-only audio
            # Whisper normally drops "um/uh"; a disfluent prompt makes it keep them so they can be cut
            initial_prompt=FILLER_PROMPT if (language or "en") == "en" else None,
        )
        segs, words = [], []
        for seg in res["segments"]:
            text = seg["text"].strip()
            if not text or _hallucinated(seg, text):
                continue
            if language in LATIN_LANGS and not _mostly_latin(text):
                continue  # forced-language Whisper still emits stray CJK on music
            segs.append({"s": seg["start"], "e": seg["end"], "t": text})
            for w in seg.get("words", []):
                if w["word"].strip():
                    words.append({"w": w["word"].strip(), "s": w["start"], "e": w["end"]})
        tr = {"language": res.get("language") or language, "segments": segs, "words": words, "detected": True}
        _free_whisper()
    cache.write_text(json.dumps(tr, ensure_ascii=False))
    return tr
