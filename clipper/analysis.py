"""One pass over the source video, cached: scene cuts, motion, faces (+ mouth movement), loudness.

Everything downstream (layouts, active-speaker tracking, scoring, B-roll timing) reads from here,
so a video is decoded for analysis once no matter how many times clips are edited.
"""
import json
import subprocess
from bisect import bisect_left, bisect_right
from pathlib import Path

import cv2
import numpy as np

from .common import FFMPEG, log, open_frames, read_exact

MODELS = Path(__file__).resolve().parent.parent / "models"
YUNET = MODELS / "face_detection_yunet_2023mar.onnx"
SAMPLE_W = 480
SCAN_FPS = 10          # scene-cut / motion resolution
FACE_EVERY = 2         # faces at 5 fps
CUT_THRESHOLD = 0.42   # Bhattacharyya distance between HSV histograms of consecutive samples
ENERGY_STEP = 0.1
ANALYSIS_VERSION = 4   # bump when the analysis gains fields; old caches are recomputed


def _mouth_patch(gray, lm, face_w):
    (rx, ry), (lx, ly) = lm
    cx, cy = (rx + lx) / 2, (ry + ly) / 2
    half_w = max(abs(lx - rx) * 0.8, face_w * 0.18)
    x0, x1 = int(cx - half_w), int(cx + half_w)
    y0, y1 = int(cy - half_w * 0.55), int(cy + half_w * 0.75)
    h, w = gray.shape
    if x1 - x0 < 6 or y1 - y0 < 4 or x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    p = cv2.resize(gray[y0:y1, x0:x1], (24, 14)).astype(np.float32)
    return (p - p.mean()) / (p.std() + 1e-3)


def _visual_pass(video, info):
    W = SAMPLE_W
    H = int(round(W * info["height"] / info["width"] / 2)) * 2
    det = cv2.FaceDetectorYN.create(str(YUNET), "", (W, H), 0.65, 0.3, 50)
    proc, head = open_frames(video, f"fps={SCAN_FPS},scale={W}:{H}")
    cuts, motion, faces, luma, detail = [], [], [], [], []
    prev_hist = prev_small = None
    tracks = {}            # track id -> (cx, cy, w, last_t, last_patch)
    next_id = 0
    i, size = 0, W * H * 3
    while True:
        buf, head = read_exact(proc, size, head)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        t = i / SCAN_FPS
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (96, 54)).astype(np.float32)
        is_cut = False
        if prev_hist is not None:
            if cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > CUT_THRESHOLD and (not cuts or t - cuts[-1] > 0.4):
                cuts.append(round(t, 2))
                is_cut = True
            motion.append(round(float(np.mean(np.abs(small - prev_small))) / 255, 4))
        else:
            motion.append(0.0)
        prev_hist, prev_small = hist, small
        luma.append(round(float(small.mean()) / 255, 3))                              # black frames / fades
        detail.append(round(float(cv2.Laplacian(gray, cv2.CV_32F).var()) / 1000, 3))  # blur / flat cards

        if i % FACE_EVERY == 0:
            if is_cut:
                tracks.clear()     # identities don't survive a camera cut
            _, dets = det.detect(frame)
            row = []
            for d in (dets if dets is not None else []):
                x, y, w, h, score = (float(v) for v in d[:5])
                lm = ((d[10], d[11]), (d[12], d[13]))  # mouth right, mouth left
                cx, cy = x + w / 2, y + h / 2
                # associate with the nearest existing track
                best, best_d = None, 0.6 * w
                for tid, (tx, ty, tw, tt, _) in tracks.items():
                    dist = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
                    if t - tt <= 1.0 and dist < best_d:
                        best, best_d = tid, dist
                if best is None:
                    best, next_id = next_id, next_id + 1
                patch = _mouth_patch(gray, lm, w)
                prev_patch = tracks.get(best, (0, 0, 0, 0, None))[4]
                act = float(np.mean(np.abs(patch - prev_patch))) if patch is not None and prev_patch is not None else 0.0
                tracks[best] = (cx, cy, w, t, patch)
                row.append([round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4),
                            round(score, 3), best, round(act, 3)])
            faces.append({"t": round(t, 2), "d": row})
        i += 1
    proc.wait()
    return {"w": W, "h": H, "scan_fps": SCAN_FPS, "cuts": cuts, "motion": motion, "faces": faces,
            "luma": luma, "detail": detail}


def _refine_cuts(video, info, cuts):
    """The 10 fps scan only knows a cut happened within the last 0.1 s. Decode every frame around
    each one and return, per cut, (out, in): the first frame that is no longer the old shot and the
    first frame that is fully the new shot. For a hard cut they are the same frame; for a dissolve
    or flash transition `in` is after the picture settles, so edits never show a stray or
    half-blended frame of the neighbouring shot."""
    fps = info.get("fps") or 30
    outs, ins = [], []
    for c in cuts:
        ss = max(0.0, c - 0.15)
        proc, head = open_frames(video, "scale=96:54", ss, 0.45)
        hists, size = [], 96 * 54 * 3
        while True:
            buf, head = read_exact(proc, size, head)
            if len(buf) < size:
                break
            hsv = cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(54, 96, 3), cv2.COLOR_BGR2HSV)
            h = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            hists.append(cv2.normalize(h, h))
        proc.wait()
        if len(hists) < 2:
            outs.append(c)
            ins.append(c)
            continue
        d = [cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA) for a, b in zip(hists, hists[1:])]
        k = int(np.argmax(d))                             # d[i]: change from frame i to frame i+1
        a = b = k
        if d[k] < 0.5:                                    # no single hard jump: a dissolve / flash transition
            lim = max(2, int(fps * 0.2))
            while a > 0 and k - a < lim and d[a - 1] > 0.08:
                a -= 1
            while b + 1 < len(d) and b - k < lim and d[b + 1] > 0.08:
                b += 1
        first = np.ceil(ss * fps - 1e-6) / fps            # time of the first decoded frame
        outs.append(round(float(first + (a + 1) / fps), 4))
        ins.append(round(float(first + (b + 1) / fps), 4))
    return outs, ins


def _beats(video, has_audio):
    """Musical beat grid + onsets from spectral flux (so montage cuts can land on the beat)."""
    if not has_audio:
        return {"beats": [], "onsets": [], "bpm": 0, "beat_strength": 0}
    sr, n_fft, hop = 11025, 1024, 256
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    if len(a) < n_fft * 4:
        return {"beats": [], "onsets": [], "bpm": 0, "beat_strength": 0}
    frames = np.lib.stride_tricks.sliding_window_view(a, n_fft)[::hop] * np.hanning(n_fft)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    fps = sr / hop
    # onsets: local peaks above a moving average
    k = int(fps * 0.5)
    avg = np.convolve(flux, np.ones(k) / k, "same")
    peaks = [i for i in range(1, len(flux) - 1) if flux[i] > avg[i] + 0.8 and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]]
    onsets, last = [], -1e9
    for i in peaks:
        if i - last >= fps * 0.1:
            onsets.append(round((i + 1) / fps, 3))
            last = i
    # tempo: autocorrelation of the flux between 70 and 180 BPM
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    lag = lo + int(np.argmax(ac[lo:hi]))
    strength = float(ac[lag] / (ac[0] + 1e-9))          # how regular the rhythm is (0..1)
    phase = max(range(lag), key=lambda p: flux[p::lag].sum())
    beats = [round((p + 1) / fps, 3) for p in range(phase, len(flux), lag)]
    return {"beats": beats, "onsets": onsets, "bpm": round(60 * fps / lag, 1), "beat_strength": round(strength, 3)}


def _energy(video, has_audio, duration):
    if not has_audio:
        return []
    sr = 8000
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    hop = int(sr * ENERGY_STEP)
    n = len(a) // hop
    rms = np.sqrt(np.mean(a[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    return [round(float(v), 1) for v in 20 * np.log10(rms)]   # dBFS per 0.1 s


class Analysis:
    def __init__(self, data, transcript):
        self.d = data
        self.tr = transcript
        self.face_t = [f["t"] for f in data["faces"]]
        self.word_s = [w["s"] for w in transcript["words"]]

    # --- queries (source seconds) ---
    def cuts_between(self, s, e):
        c = self.d["cuts"]
        return c[bisect_left(c, s):bisect_right(c, e)]

    def faces_between(self, s, e):
        f = self.d["faces"]
        return f[bisect_left(self.face_t, s):bisect_right(self.face_t, e)]

    def faces_at(self, t):
        if not self.face_t:
            return []
        i = min(bisect_left(self.face_t, t), len(self.face_t) - 1)
        return self.d["faces"][i]["d"]

    def motion_between(self, s, e):
        m = self.d["motion"]
        return m[int(s * SCAN_FPS):int(e * SCAN_FPS) + 1]

    def luma_between(self, s, e):
        return self.d.get("luma", [])[int(s * SCAN_FPS):int(e * SCAN_FPS) + 1]

    def detail_between(self, s, e):
        return self.d.get("detail", [])[int(s * SCAN_FPS):int(e * SCAN_FPS) + 1]

    def energy_between(self, s, e):
        en = self.d["energy"]
        return en[int(s / ENERGY_STEP):int(e / ENERGY_STEP) + 1]

    def words_between(self, s, e):
        w = self.tr["words"]
        return [x for x in w[bisect_left(self.word_s, s - 1):] if x["s"] >= s and x["e"] <= e]

    def face_share(self, s, e):
        rows = self.faces_between(s, e)
        return sum(1 for r in rows if r["d"]) / len(rows) if rows else 0.0

    def scenes(self, s, e):
        """Split [s, e] at camera cuts."""
        pts = [s] + [c for c in self.cuts_between(s, e) if s + 0.2 < c < e - 0.2] + [e]
        return list(zip(pts[:-1], pts[1:]))


def analyze(video, info, transcript, cache_dir):
    cache = Path(cache_dir) / "analysis.json"
    data = json.loads(cache.read_text()) if cache.exists() else {}
    if data.get("version") != ANALYSIS_VERSION:
        log("Analysing video: shots, motion, faces, loudness, beats")
        data = _visual_pass(video, info)
        data["cuts_coarse"] = data["cuts"]
        data["cuts"], data["cuts_in"] = _refine_cuts(video, info, data["cuts"])
        data["energy"] = _energy(video, info["has_audio"], info["duration"])
        data.update(_beats(video, info["has_audio"]))
        data["fps"] = info.get("fps") or 30
        data["version"] = ANALYSIS_VERSION
        cache.write_text(json.dumps(data, separators=(",", ":")))
    return Analysis(data, transcript)
