"""Clip audio: stitch the kept pieces, clean up speech, mix music/SFX/voice-over, normalize loudness."""
import json
import subprocess
from pathlib import Path

from .common import FFMPEG

MODELS = Path(__file__).resolve().parent.parent / "models"
RNNOISE = MODELS / "cb.rnnn"
SR = 48000


def speech_chain(a):
    """Speech enhancement: rumble cut, noise reduction, presence, gentle compression."""
    chain = ["highpass=f=80"]
    if a.get("denoise"):
        chain.append(f"arnndn=m='{RNNOISE}':mix=0.85" if RNNOISE.exists() else "afftdn=nr=12:nf=-40")
    if a.get("enhance"):
        chain += ["afftdn=nr=6:nf=-45", "equalizer=f=3000:t=q:w=1.2:g=2.5", "equalizer=f=250:t=q:w=1:g=-1.5",
                  "acompressor=threshold=-20dB:ratio=3:attack=8:release=120:makeup=2"]
    return chain


def render_audio(spec, info, pieces, total, out_wav, extra=None, joins=None):
    """extra: optional dict with 'beeps' [(t0,t1)], 'sfx' [(t, path)], 'voice' path, 'music' path (output timeline)."""
    a, extra = spec["audio"], extra or {}
    total = max(total, 0.1)
    if info["has_audio"]:                   # every piece keeps its own sound, so picture and audio stay in sync
        inputs = ["-i", spec["source"]]
        parts, labels = [], []
        for i, (s, e, _) in enumerate(pieces):
            d = e - s
            fade = min(0.012, d / 4)
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,aresample={SR},"
                         f"afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}[p{i}]")
            labels.append(f"[p{i}]")
        joins = {i: d for i, d in (joins or {}).items() if d > 0.02}
        if not joins:
            graph = parts + [f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[cat]"]
        else:                                  # dissolve: the sound crossfades over the same overlap
            graph, cur = list(parts), "p0"
            for i in range(1, len(labels)):
                nxt = f"j{i}"
                if i in joins:
                    graph.append(f"[{cur}][p{i}]acrossfade=d={joins[i]:.3f}:c1=tri:c2=tri[{nxt}]")
                else:
                    graph.append(f"[{cur}][p{i}]concat=n=2:v=0:a=1[{nxt}]")
                cur = nxt
            graph.append(f"[{cur}]anull[cat]")
    else:                                   # silent source: start from silence so music/voice still work
        inputs = ["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo"]
        graph = [f"[0:a]atrim=0:{total:.3f}[cat]"]
    cur = "cat"

    voice = extra.get("voice")
    if voice and a["voiceover"].get("mix") == "replace":
        inputs += ["-i", str(voice)]
        graph.append(f"[cat]anullsink;[{_input_count(inputs) - 1}:a]aresample={SR},apad[vo]")
        cur = "vo"
    else:
        chain = speech_chain(a)
        for t0, t1 in extra.get("beeps", []):                          # censor: silence the word, add a beep below
            chain.append(f"volume=enable='between(t,{t0:.3f},{t1:.3f})':volume=0")
        graph.append(f"[{cur}]{','.join(chain)}[sp]")
        cur = "sp"
        if voice:                                                      # voice-over on top of original
            inputs += ["-i", str(voice)]
            graph.append(f"[{_input_count(inputs) - 1}:a]aresample={SR}[vo];[sp]volume=0.35[spd];[spd][vo]amix=inputs=2:normalize=0[mixv]")
            cur = "mixv"

    beeps = extra.get("beeps", [])
    if beeps:
        tones = "+".join(f"between(t,{t0:.3f},{t1:.3f})" for t0, t1 in beeps)
        graph.append(f"sine=f=1000:r={SR}:d={total:.3f},volume='0.25*({tones})':eval=frame[bp];[{cur}][bp]amix=inputs=2:normalize=0[wb]")
        cur = "wb"

    sfx = extra.get("sfx", [])
    if sfx:
        sfx_labels = []
        for j, (t, path) in enumerate(sfx):
            inputs += ["-i", str(path)]
            idx = _input_count(inputs) - 1
            graph.append(f"[{idx}:a]aresample={SR},volume={a.get('sfx_volume', 0.5)},adelay={int(t * 1000)}:all=1[sx{j}]")
            sfx_labels.append(f"[sx{j}]")
        graph.append(f"[{cur}]{''.join(sfx_labels)}amix=inputs={len(sfx_labels) + 1}:normalize=0:duration=first[ws]")
        cur = "ws"

    music = extra.get("music")
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        idx = _input_count(inputs) - 1
        graph.append(f"[{idx}:a]aresample={SR},volume={a.get('music_volume', 0.12)},atrim=0:{total:.3f}[mu]")
        if a.get("duck", True):
            graph.append(f"[{cur}]asplit=2[k1][k2];[mu][k1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[mud];"
                         f"[k2][mud]amix=inputs=2:normalize=0:duration=first[wm]")
        else:
            graph.append(f"[{cur}][mu]amix=inputs=2:normalize=0:duration=first[wm]")
        cur = "wm"

    fade = min(float((spec.get("ending") or {}).get("fade") or 0), total / 3)
    fade_tag = f",afade=t=out:st={total - fade:.3f}:d={fade:.3f}" if fade > 0.01 else ""   # no hard stop at the end
    graph.append(f"[{cur}]atrim=0:{total:.3f},aformat=channel_layouts=stereo{fade_tag}[out]")
    mix = Path(out_wav).with_suffix(".mix.wav") if a.get("loudnorm", True) else Path(out_wav)
    subprocess.run([FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph), "-map", "[out]",
                    "-ar", str(SR), str(mix)], check=True)
    if a.get("loudnorm", True):
        normalize_loudness(mix, out_wav)
        mix.unlink(missing_ok=True)


def normalize_loudness(src, dst, target=-14.0, peak=-1.5):
    """Two-pass EBU R128 loudness normalization to the social-platform target (-14 LUFS)."""
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(src), "-af",
                            f"loudnorm=I={target}:TP={peak}:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rfind("{"):probe.rfind("}") + 1])
    if float(m["input_i"]) < -70:          # silence: nothing to normalize
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), str(dst)], check=True)
        return
    af = (f"loudnorm=I={target}:TP={peak}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(SR), str(dst)], check=True)


KOKORO_MODEL = MODELS / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS / "voices-v1.0.bin"
_kokoro = None


def tts_engine():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    return _kokoro


def list_voices():
    """Voice names straight from the voices file (no need to load the 300 MB model)."""
    try:
        import numpy as np
        with np.load(KOKORO_VOICES) as z:
            return sorted(z.files)
    except Exception:
        return []


def synthesize(text, out_wav, voice="af_heart", lang="en-us", speed=1.0):
    """Local AI voice (Kokoro, open-source). Returns duration in seconds."""
    import soundfile as sf
    samples, sr = tts_engine().create(text, voice=voice or "af_heart", speed=speed, lang=lang or "en-us")
    sf.write(str(out_wav), samples, sr)
    return len(samples) / sr


def _input_count(args):
    return sum(1 for x in args if x == "-i")
