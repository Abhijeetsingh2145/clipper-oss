import numpy as np
import soundfile as sf

from clipper.silence import AFTER_SHARE, silence_cuts

SR = 16000


def recording(tmp_path, parts, noise_db=-60, name="rec"):
    """parts: [(seconds, 'speech'|'quiet'|'soft')]. Speech is a noisy -18 dB voice-like tone,
    'soft' a -38 dB whisper, 'quiet' the room noise floor. Written as an mp4 audio track."""
    rng = np.random.default_rng(0)
    chunks = []
    for dur, kind in parts:
        n = int(dur * SR)
        t = np.arange(n) / SR
        noise = rng.normal(0, 10 ** (noise_db / 20), n)
        if kind == "speech":
            x = 10 ** (-18 / 20) * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)) + noise
        elif kind == "soft":
            x = 10 ** (-38 / 20) * np.sin(2 * np.pi * 220 * t) + noise
        else:
            x = noise
        chunks.append(x)
    wav = tmp_path / f"{name}.wav"
    sf.write(wav, np.concatenate(chunks).astype(np.float32), SR, subtype="PCM_16")
    return wav


def test_long_pause_is_shortened_with_more_kept_after_speech(tmp_path):
    src = recording(tmp_path, [(3, "speech"), (1.5, "quiet"), (3, "speech")])
    cuts = silence_cuts(src, 0, 7.5, [], keep=0.35)
    assert len(cuts) == 1
    a, b = cuts[0]
    assert abs(a - (3 + 0.35 * AFTER_SHARE)) < 0.06                     # ~0.30 s kept after speech
    assert abs(b - (4.5 - 0.35 * (1 - AFTER_SHARE))) < 0.06             # ~0.05 s kept before the next line


def test_short_pauses_are_left_alone(tmp_path):
    src = recording(tmp_path, [(3, "speech"), (0.25, "quiet"), (3, "speech")])
    assert silence_cuts(src, 0, 6.25, [], keep=0.35) == []


def test_dead_air_at_the_edges_is_trimmed(tmp_path):
    src = recording(tmp_path, [(1.0, "quiet"), (3, "speech"), (1.0, "quiet")])
    cuts = silence_cuts(src, 0, 5.0, [], keep=0.35)
    assert cuts[0][0] == 0 and abs(cuts[0][1] - 0.95) < 0.08            # lead-in removed up to the speech
    assert abs(cuts[-1][1] - 5.0) < 0.02                                 # tail removed to the end


def test_a_quiet_word_inside_a_pause_is_never_cut(tmp_path):
    src = recording(tmp_path, [(3, "speech"), (0.9, "quiet"), (0.3, "soft"), (0.8, "quiet"), (3, "speech")])
    word = {"w": "yeah", "s": 3.9, "e": 4.2}                             # a whisper-level word, 22 dB over the room
    cuts = silence_cuts(src, 0, 8.0, [word], keep=0.35)
    assert not any(a < word["e"] and word["s"] < b for a, b in cuts)
    assert len(cuts) == 2                                                # the pause either side is still cut


def test_whisper_timing_stretched_over_silence_does_not_block_the_cut(tmp_path):
    src = recording(tmp_path, [(3, "speech"), (2.0, "quiet"), (3, "speech")])
    phantom = {"w": "so", "s": 3.0, "e": 4.6}                            # Whisper stretched a word over the pause
    cuts = silence_cuts(src, 0, 8.0, [phantom], keep=0.35)
    assert len(cuts) == 1 and cuts[0][1] - cuts[0][0] > 1.5


def test_too_noisy_to_calibrate_returns_none(tmp_path):
    src = recording(tmp_path, [(3, "speech"), (1.5, "quiet"), (3, "speech")], noise_db=-24)
    assert silence_cuts(src, 0, 7.5, [], keep=0.35) is None
