"""Animated captions as ASS subtitles (burned in by ffmpeg/libass).

Templates set the look; any field in spec["captions"] overrides the template. Sizes are written
for a 1080x1920 frame and scaled for other aspect ratios.
"""
import re

NAMED = {
    "white": "#FFFFFF", "black": "#000000", "yellow": "#FFE600", "red": "#FF2D2D", "green": "#2DFF5A",
    "blue": "#2D8CFF", "orange": "#FF8A00", "pink": "#FF4FD8", "purple": "#9B5CFF", "cyan": "#00E5FF",
}

# style: highlight = active word recoloured+enlarged | karaoke = colour sweeps through words |
#        plain = no per-word effect | single = one word at a time, popping in
TEMPLATES = {
    "bold_pop": dict(label="Bold Pop", font="Arial Black", size=88, color="#FFFFFF", highlight="#FFE600", keyword="#2DFF5A",
                     outline=7, shadow=3, upper=True, words=3, style="highlight", pos="lower", box=False),
    "karaoke": dict(label="Karaoke", font="Arial Black", size=82, color="#FFFFFF", highlight="#2DFF5A", keyword="#FFE600",
                    outline=6, shadow=2, upper=True, words=4, style="karaoke", pos="lower", box=False),
    "boxed": dict(label="Boxed", font="Arial Black", size=74, color="#FFFFFF", highlight="#FFE600", keyword="#FF8A00",
                  outline=16, shadow=0, upper=False, words=4, style="highlight", pos="lower", box=True),
    "minimal": dict(label="Minimal", font="Helvetica Neue", size=66, color="#FFFFFF", highlight="#FFFFFF", keyword="#FFE600",
                    outline=0, shadow=4, upper=False, words=6, style="plain", pos="lower", box=False),
    "neon": dict(label="Neon", font="Futura", size=86, color="#E9FBFF", highlight="#FF4FD8", keyword="#00E5FF",
                 outline=5, shadow=0, upper=True, words=3, style="highlight", pos="middle", box=False, glow="#00E5FF"),
    "word_pop": dict(label="One word", font="Impact", size=128, color="#FFFFFF", highlight="#FFE600", keyword="#FFE600",
                     outline=9, shadow=4, upper=True, words=1, style="single", pos="middle", box=False),
    "subtitle": dict(label="Subtitle", font="Arial", size=58, color="#FFFFFF", highlight="#FFFFFF", keyword="#FFFFFF",
                     outline=3, shadow=1, upper=False, words=8, style="plain", pos="bottom", box=False),
}
SPEAKER_COLORS = ["#FFFFFF", "#7FDBFF", "#FFB3D9", "#B6FF8F"]
POSITIONS = {"top": (8, 300), "middle": (5, 0), "lower": (2, 620), "bottom": (2, 300)}


def ass_color(c, alpha="00"):
    c = NAMED.get(str(c).strip().lower(), str(c).strip()).lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        c = "FFFFFF"
    return f"&H{alpha}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean(word):
    return word.replace("{", "(").replace("}", ")").replace("\\", "")


def censor_word(w):
    core = re.sub(r"[^\w']", "", w)
    return w.replace(core, core[0] + "*" * (len(core) - 1)) if len(core) > 1 else w


def chunk_words(words, per):
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or w["s"] - cur[-1]["e"] > 0.6 or w.get("spk") != cur[-1].get("spk")):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["w"][-1:] in ".?!," and len(cur) >= 1:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def resolve_style(cap):
    """Template values with the clip's overrides applied."""
    t = dict(TEMPLATES.get(cap.get("template") or "bold_pop", TEMPLATES["bold_pop"]))
    for k_spec, k_t in (("font", "font"), ("color", "color"), ("highlight", "highlight"), ("position", "pos")):
        if cap.get(k_spec):
            t[k_t] = cap[k_spec]
    if cap.get("uppercase") is not None:
        t["upper"] = bool(cap["uppercase"])
    if cap.get("words_per_line"):
        t["words"] = int(cap["words_per_line"])
    if t["style"] == "single":
        t["words"] = 1
    return t


def build_ass(words, cap, duration, size, title=None, watermark="", censor_set=()):
    """words: output-timeline [{w,s,e,kw?,spk?}]. cap: spec["captions"]. size: (w, h)."""
    W, H = size
    k = min(W / 1080, H / 1350, 1.0)
    st = resolve_style(cap)
    align, margin_v = POSITIONS.get(st["pos"], POSITIONS["lower"])
    margin_v = int(margin_v * H / 1920)
    if cap.get("margin_v_px") is not None:            # a reel frame keeps captions inside/below the picture
        align, margin_v = 2, int(cap["margin_v_px"])
    base, hl, kwc = ass_color(st["color"]), ass_color(st["highlight"]), ass_color(st["keyword"])
    outline_c = ass_color(st.get("glow", "#000000"))
    border_style = 3 if st["box"] else 1
    back = "&H64000000" if st["box"] else "&H80000000"
    fs = int(st["size"] * k)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{st['font']},{fs},{hl if st['style'] == 'karaoke' else base},{base},{outline_c},{back},-1,0,0,0,100,100,0,0,"
        f"{border_style},{max(0, int(st['outline'] * k))},{int(st['shadow'] * k)},{align},{int(70 * k)},{int(70 * k)},{margin_v},1",
        f"Style: Title,{st['font']},{int(60 * k)},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,{int(16 * k)},0,8,{int(90 * k)},{int(90 * k)},{int(200 * H / 1920)},1",
        f"Style: Mark,Arial,{int(40 * k)},&H50FFFFFF,&H50FFFFFF,&H64000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,3,{int(50 * k)},{int(50 * k)},{int(90 * H / 1920)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    offset = float(cap.get("offset") or 0)
    glow = "\\blur6" if st.get("glow") else ""
    if cap.get("enabled", True) and words:
        words = [{**w, "s": w["s"] + offset, "e": w["e"] + offset} for w in words]
        chunks = chunk_words(words, max(1, st["words"]))
        for ci, ch in enumerate(chunks):
            nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else duration
            chunk_end = min(nxt, ch[-1]["e"] + 0.4)

            def text_of(w):
                t = w["w"]
                if censor_set and re.sub(r"[^\w']", "", t).lower() in censor_set:
                    t = censor_word(t)
                return clean(t.upper() if st["upper"] else t)

            def base_tag(w):
                if cap.get("speaker_colors") and w.get("spk") is not None:
                    return f"{{\\c{ass_color(SPEAKER_COLORS[int(w['spk']) % len(SPEAKER_COLORS)])}}}"
                if cap.get("keywords", True) and w.get("kw"):
                    return f"{{\\c{kwc}}}"
                return ""

            texts = [text_of(w) for w in ch]
            if st["style"] == "karaoke":
                parts = []
                for i, w in enumerate(ch):
                    dur_cs = int(max(0.05, (ch[i + 1]["s"] if i + 1 < len(ch) else w["e"]) - w["s"]) * 100)
                    parts.append(f"{{\\kf{dur_cs}}}{texts[i]}")
                lines.append(f"Dialogue: 0,{ts(ch[0]['s'])},{ts(chunk_end)},Cap,,0,0,0,,{{\\fad(40,0){glow}}}{' '.join(parts)}")
                continue
            if st["style"] == "plain":
                body = " ".join(f"{base_tag(w)}{texts[i]}{{\\r}}" if base_tag(w) else texts[i] for i, w in enumerate(ch))
                lines.append(f"Dialogue: 0,{ts(ch[0]['s'])},{ts(chunk_end)},Cap,,0,0,0,,{{\\fad(60,60){glow}}}{body}")
                continue
            for i, w in enumerate(ch):
                s = ch[0]["s"] if i == 0 else w["s"]
                e = ch[i + 1]["s"] if i + 1 < len(ch) else chunk_end
                if e <= s:
                    continue
                if st["style"] == "single":
                    lines.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{{\\fscx70\\fscy70\\t(0,90,\\fscx100\\fscy100){glow}}}"
                                 f"{base_tag(w)}{texts[i]}")
                    continue
                parts = [f"{{\\c{hl}\\fscx112\\fscy112}}{t}{{\\r}}" if j == i else (f"{base_tag(ch[j])}{t}{{\\r}}" if base_tag(ch[j]) else t)
                         for j, t in enumerate(texts)]
                pop = "{\\fad(40,0)}" if i == 0 else ""
                lines.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{pop}{{{glow}}}{' '.join(parts)}" if glow else
                             f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    if title and title.get("show") and title.get("text"):
        secs = float(title.get("seconds", 4) or 0)
        whole = secs <= 0 or secs >= duration                      # the hook stays for the whole clip
        end = duration if whole else min(duration, secs)
        fade = "{\\fad(150,0)}" if whole else "{\\fad(0,250)}"
        lines.append(f"Dialogue: 1,{ts(0)},{ts(end)},Title,,0,0,0,,{fade}{clean(title['text'])}")
    if watermark:
        lines.append(f"Dialogue: 2,{ts(0)},{ts(duration)},Mark,,0,0,0,,{clean(watermark)}")
    return "\n".join(lines) + "\n"
