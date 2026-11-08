/* Clip editor: timeline, transcript editing, panels, AI Producer, versions, shortcuts */
(function () {
  let ED = null;              // { job, name, spec, versions, dirty, actions:[], meta, srcDur }
  let keyHandler = null, statusTimer = null;

  window.editorCleanup = () => {
    if (keyHandler) document.removeEventListener("keydown", keyHandler);
    keyHandler = null; clearTimeout(statusTimer);
    if (ED && ED.dirty) saveSpec("unsaved edits (auto-saved on leave)");
  };

  /* ---------- timeline math (mirrors spec.py) ---------- */
  function holes(spec) {
    const wordHoles = spec.words.filter(w => w.deleted).map(w => [w.s, w.e]);
    return [...spec.cuts.map(c => [c[0], c[1]]), ...wordHoles].sort((a, b) => a[0] - b[0]);
  }
  function pieces(spec) {
    const H = holes(spec), out = []; let t = 0;
    for (const seg of spec.segments) {
      let cur = seg.start;
      for (const [hs, he] of H) {
        if (he <= cur || hs >= seg.end) continue;
        if (hs > cur && hs - cur >= 0.05) { out.push([cur, hs, t]); t += hs - cur; }
        cur = Math.max(cur, he);
      }
      if (seg.end - cur >= 0.05) { out.push([cur, seg.end, t]); t += seg.end - cur; }
    }
    return out;
  }
  const duration = spec => pieces(spec).reduce((a, [s, e]) => a + e - s, 0);
  function outToSrc(spec, t) { for (const [s, e, o] of pieces(spec)) if (t >= o && t <= o + e - s) return s + (t - o); return null; }

  /* ---------- load ---------- */
  window.viewEditor = async function (job, name) {
    const [clip, meta, jobInfo] = await Promise.all([api(`/api/jobs/${job}/clips/${name}`), loadMeta(), api(`/api/jobs/${job}`)]);
    ED = { job, name, spec: clip.spec, versions: clip.versions, dirty: false, actions: [], meta, rules: jobInfo.rules,
           srcDur: clip.source_duration || (clip.spec.segments.at(-1).end + 60), hasPreview: clip.has_preview, tab: "captions", wordMode: "cut" };
    render();
    pollStatus();
  };

  function videoSrc() {
    const v = ED.spec.v || 0;
    return ED.hasPreview && ED.lastRender === "preview" ? `/files/${ED.job}/${ED.name}.preview.mp4?v=${Date.now()}` : `/files/${ED.job}/${ED.name}.mp4?v=${v}-${ED.bust || 0}`;
  }

  function render() {
    const s = ED.spec;
    view.innerHTML = `
    <div class="row" style="margin-bottom:14px"><a class="btn sm" href="#/job/${ED.job}">← Clips</a><h1 class="grow">${esc(s.title?.text || ED.name)}</h1>
      <span class="hint" id="dirty"></span>
      <button class="btn sm" id="undo" title="⌘Z">↶ Undo</button><button class="btn sm" id="redo" title="⇧⌘Z">↷ Redo</button></div>
    <div class="editor">
      <div class="player stack">
        <video id="vid" src="${videoSrc()}" controls playsinline></video>
        <div class="row"><button class="btn grow" id="prev">Save &amp; preview</button><button class="btn primary grow" id="exp">Export final</button></div>
        <div class="hint" id="rstat"></div>
        <div class="card stack"><h2>AI Producer</h2>
          <div class="aibox"><input type="text" id="ai" class="grow" placeholder="e.g. make captions yellow and add zooms"><button class="btn" id="aigo">Do it</button></div>
          <div class="hint" id="aires">Describe any edit: captions, trim, framing, zooms, audio clean-up, cut a phrase, better hook…</div>
          <div class="chips">${["keywords", "emoji", "title", "hook", "broll"].map(t => `<button class="btn sm" data-regen="${t}">New ${t}</button>`).join("")}</div>
        </div>
        <div class="card stack"><h2>History</h2><div class="history" id="hist"></div></div>
        <div class="hint">Keys: <span class="kbd">Space</span> play · <span class="kbd">J</span>/<span class="kbd">L</span> ±2s · <span class="kbd">I</span>/<span class="kbd">O</span> set start/end at playhead · <span class="kbd">⌘S</span> save + preview · <span class="kbd">⌘Z</span> undo</div>
      </div>
      <div class="stack">
        <div class="card stack"><div class="row"><h2 class="grow" style="margin:0">Timeline</h2><span class="hint" id="dur"></span>
          <button class="btn sm" id="addsec">＋ Add section</button></div>
          <div class="timeline" id="tl"></div>
          <div id="segs" class="stack"></div>
        </div>
        <div class="card stack"><div class="row"><h2 class="grow" style="margin:0">Transcript</h2>
          <div class="tabs" id="wm" style="width:auto">${[["cut", "Click = cut"], ["kw", "Click = highlight"], ["emoji", "Click = emoji"], ["edit", "Click = fix text"]].map(([k, l]) => `<button data-wm="${k}" class="${ED.wordMode === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
          <div class="words" id="words"></div></div>
        <div class="card"><div class="panel-tabs" id="ptabs">${[["captions", "Captions"], ["framing", "Framing"], ["audio", "Audio"], ["visuals", "Visuals"], ["post", "Post"], ["score", "Score"]].map(([k, l]) => `<button data-t="${k}" class="${ED.tab === k ? "on" : ""}">${l}</button>`).join("")}</div>
          <div id="panel"></div></div>
      </div>
    </div>`;
    drawTimeline(); drawSegments(); drawWords(); drawPanel(); drawHistory(); markDirty();
    bind();
  }

  /* ---------- timeline ---------- */
  function drawTimeline() {
    const s = ED.spec, tl = $("#tl");
    const lo = Math.max(0, Math.min(...s.segments.map(g => g.start)) - 15), hi = Math.min(ED.srcDur, Math.max(...s.segments.map(g => g.end)) + 15);
    ED.view = [lo, hi];
    const X = t => `${((t - lo) / (hi - lo)) * 100}%`;
    tl.innerHTML = s.segments.map((g, i) => `<div class="seg" data-i="${i}" style="left:${X(g.start)};width:calc(${X(g.end)} - ${X(g.start)})"><div class="h l" data-h="start"></div><div class="h r" data-h="end"></div></div>`).join("")
      + holes(s).map(([a, b]) => `<div class="cut" style="left:${X(a)};width:calc(${X(b)} - ${X(a)})"></div>`).join("")
      + `<div class="ph" id="ph" style="left:${X(s.segments[0].start)}"></div><span class="lbl" style="left:4px">${fmt(lo)}</span><span class="lbl" style="right:4px">${fmt(hi)}</span>`;
    $("#dur").textContent = `${duration(s).toFixed(1)}s`;
    $$(".h", tl).forEach(h => h.onpointerdown = e => {
      e.preventDefault(); const i = +h.parentElement.dataset.i, edge = h.dataset.h, rect = tl.getBoundingClientRect();
      const move = ev => {
        const t = lo + ((ev.clientX - rect.left) / rect.width) * (hi - lo);
        const g = ED.spec.segments[i];
        if (edge === "start") g.start = +Math.max(0, Math.min(t, g.end - 0.5)).toFixed(2); else g.end = +Math.min(ED.srcDur, Math.max(t, g.start + 0.5)).toFixed(2);
        drawTimeline(); drawSegments();
      };
      const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); refreshWords(); change(`Trimmed section ${i + 1}`); };
      document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
    });
  }
  function drawSegments() {
    const s = ED.spec;
    $("#segs").innerHTML = s.segments.map((g, i) => `<div class="row" data-i="${i}"><span class="hint" style="width:70px">Section ${i + 1}</span>
      <input type="text" data-k="start" value="${fmt(g.start)}" style="width:90px"> → <input type="text" data-k="end" value="${fmt(g.end)}" style="width:90px">
      <span class="hint grow">${(g.end - g.start).toFixed(1)}s</span>
      <button class="btn sm" data-mv="-1" ${i ? "" : "disabled"}>↑</button><button class="btn sm" data-mv="1" ${i < s.segments.length - 1 ? "" : "disabled"}>↓</button>
      <button class="btn sm danger" data-rm ${s.segments.length > 1 ? "" : "disabled"}>✕</button></div>`).join("");
    $$("#segs [data-k]").forEach(inp => inp.onchange = () => { const i = +inp.closest("[data-i]").dataset.i; ED.spec.segments[i][inp.dataset.k] = parseTime(inp.value); refreshWords(); drawTimeline(); drawSegments(); change(`Set section ${i + 1} ${inp.dataset.k}`); });
    $$("#segs [data-mv]").forEach(b => b.onclick = () => { const i = +b.closest("[data-i]").dataset.i, j = i + +b.dataset.mv; const a = ED.spec.segments; [a[i], a[j]] = [a[j], a[i]]; refreshWords(); drawTimeline(); drawSegments(); change(`Reordered sections`); });
    $$("#segs [data-rm]").forEach(b => b.onclick = () => { const i = +b.closest("[data-i]").dataset.i; ED.spec.segments.splice(i, 1); refreshWords(); drawTimeline(); drawSegments(); change(`Removed section ${i + 1}`); });
  }
  async function refreshWords() {
    // pull transcript words for the (new) section ranges, keeping edits on words we already had
    const roman = ED.spec.captions.script === "roman";
    if (!ED.transcript || (roman && !ED.transcript.words.some(w => w.w_roman)))
      ED.transcript = await api(`/api/jobs/${ED.job}/transcript${roman ? "?roman=1" : ""}`);
    const tr = ED.transcript;
    const text = w => (roman && w.w_roman) || w.w;
    const old = new Map(ED.spec.words.map(w => [`${w.s.toFixed(2)}|${w.e.toFixed(2)}`, w]));
    const words = [];
    for (const g of ED.spec.segments) for (const w of tr.words) if (w.s >= g.start && w.e <= g.end) {
      const k = `${w.s.toFixed(2)}|${w.e.toFixed(2)}`, prev = old.get(k);
      // switching script swaps the spelling but keeps deletions and keyword marks
      words.push(prev ? { ...prev, w: ED.scriptSwap ? text(w) : prev.w } : { w: text(w), s: +w.s.toFixed(3), e: +w.e.toFixed(3), deleted: false });
    }
    ED.spec.words = words; drawWords(); drawTimeline();
  }

  /* ---------- transcript ---------- */
  function drawWords() {
    const s = ED.spec, H = s.cuts;
    $("#words").innerHTML = s.words.map((w, i) => {
      const cut = H.some(([a, b]) => w.s >= a - 0.01 && w.e <= b + 0.01);
      return `<span data-i="${i}" class="${w.deleted ? "del" : ""} ${w.kw ? "kw" : ""} ${cut ? "cutw" : ""}" title="${fmt(w.s)}">${esc(w.w)}${w.emoji ? " " + esc(w.emoji) : ""}</span>`;
    }).join(" ") || `<span class="hint">No speech in this clip.</span>`;
  }
  function onWord(i) {
    const w = ED.spec.words[i];
    if (ED.wordMode === "cut") { w.deleted = !w.deleted; change(`${w.deleted ? "Cut" : "Restored"} “${w.w}”`); }
    else if (ED.wordMode === "kw") { w.kw = !w.kw; change(`${w.kw ? "Highlighted" : "Un-highlighted"} “${w.w}”`); }
    else if (ED.wordMode === "emoji") { const e = prompt(`Emoji for “${w.w}” (empty to remove)`, w.emoji || ""); if (e === null) return; w.emoji = e.trim(); ED.spec.captions.emoji = true; change(`Emoji on “${w.w}”`); }
    else { const t = prompt("Correct the caption text", w.w); if (t === null || !t.trim()) return; const old = w.w; w.w = t.trim(); change(`Caption “${old}” → “${w.w}”`); }
    drawWords(); drawTimeline();
  }

  /* ---------- panels ---------- */
  const val = (o, k, d = "") => (o && o[k] != null ? o[k] : d);
  function drawPanel() {
    const s = ED.spec, c = s.captions, a = s.audio, m = ED.meta, L = s.layout, P = s.meta.post || {};
    const tpl = Object.entries(m.templates).map(([k, l]) => opt(k, l, c.template)).join("");
    const panels = {
      captions: `<div class="grid3"><div><label class="f">Template</label><select data-p="captions.template">${tpl}</select></div>
          <div><label class="f">Caption script</label><select id="cscript">${[["roman", "Roman letters (Hinglish)"], ["native", "Original script"]].map(([v, l]) => opt(v, l, c.script === "roman" ? "roman" : "native")).join("")}</select></div>
          <div><label class="f">Position</label><select data-p="captions.position">${["", "top", "middle", "lower", "bottom"].map(p => opt(p, p || "Template default", c.position)).join("")}</select></div>
          <div><label class="f">Words per line</label><input type="number" min="0" max="8" data-p="captions.words_per_line" data-num value="${c.words_per_line || ""}" placeholder="default"></div></div>
        <div class="grid3"><div><label class="f">Font</label><select data-p="captions.font">${opt("", "Template default", c.font)}${["Arial Black", "Impact", "Futura", "Helvetica Neue", "Avenir Next"].concat(m.assets.font.map(f => f.name.replace(/\.(ttf|otf|ttc)$/i, ""))).map(f => opt(f, f, c.font)).join("")}</select></div>
          <div><label class="f">Text colour</label><input type="color" data-p="captions.color" value="${c.color || "#ffffff"}"></div>
          <div><label class="f">Highlight</label><input type="color" data-p="captions.highlight" value="${c.highlight || "#ffe600"}"></div></div>
        <div><label class="f">Timing offset: <b id="offv">${(+c.offset || 0).toFixed(2)}s</b> (negative = earlier)</label><input type="range" min="-1" max="1" step="0.05" data-p="captions.offset" data-num value="${c.offset || 0}"></div>
        <div class="grid2"><div>${tg("captions.enabled", "Show captions", c.enabled)}${tg("captions.uppercase", "ALL CAPS", c.uppercase !== false)}${tg("captions.keywords", "Highlight key words", c.keywords !== false)}</div>
          <div>${tg("captions.emoji", "Emoji", c.emoji)}${tg("captions.speaker_colors", "Colour per speaker", c.speaker_colors)}${tg("captions.censor", "Censor profanity", c.censor)}</div></div>
        <div><label class="f">Extra words to censor (comma separated)</label><input type="text" data-p="captions.censor_words" data-list value="${esc((c.censor_words || []).join(", "))}"></div>
        <div><label class="f">On-screen hook (stays on screen the whole clip)</label>
          <div class="row"><input type="text" class="grow" style="width:auto" data-p="title.text" value="${esc(s.title.text || "")}" placeholder="e.g. When mummy becomes papa's spokesperson">
          <button class="btn sm" id="newhook">✨ New hook</button></div></div>
        <div class="grid2"><div>${tg("title.show", "Show the hook", s.title.show)}</div>
          <div>${tg("title.whole", "Keep it for the whole clip", !(+s.title.seconds > 0))}</div></div>
        <div><label class="f">Watermark</label><input type="text" data-p="watermark" value="${esc(s.watermark || "")}"></div>`,
      framing: `<div class="grid2"><div><label class="f">Aspect ratio</label><select data-p="aspect">${m.aspects.map(x => opt(x, x === "original" ? "Original" : x, s.aspect)).join("")}</select></div>
          <div><label class="f">Layout</label><select data-p="layout.mode">${[["auto", "Auto (switches per shot)"], ["track", "Follow active speaker"], ["split", "Split screen (2 people)"], ["gameplay", "Gameplay + facecam"], ["screen", "Screen share + speaker"], ["fit", "Full frame + blur"], ["center", "Center crop"], ["manual", "Manual crop"]].map(([v, l]) => opt(v, l, L.mode)).join("")}</select></div></div>
        <div><label class="f">Manual crop position (Manual layout): ${Math.round((L.manual_x ?? .5) * 100)}%</label><input type="range" min="0" max="1" step="0.01" data-p="layout.manual_x" data-num value="${L.manual_x ?? .5}"></div>
        <div><label class="f">Who to follow (Follow speaker): ${L.target_x == null ? "whoever is talking" : `face nearest ${Math.round(L.target_x * 100)}% from the left`}</label>
          <div class="row"><input type="range" min="0" max="1" step="0.01" id="tx" value="${L.target_x ?? .5}" class="grow"><button class="btn sm" id="txclr">Auto</button></div></div>
        <div>${tg("layout.switching", "Allow split screen when two people talk (Auto)", L.switching !== false)}</div>
        <div><label class="f">Facecam box for Gameplay (x, y, w, h as 0–1; blank = auto-detect)</label><input type="text" id="fc" value="${L.facecam ? L.facecam.join(", ") : ""}" placeholder="auto"></div>`,
      audio: `<div class="grid2"><div>${tg("audio.enhance", "Enhance speech")}${tg("audio.denoise", "Remove background noise")}${tg("audio.loudnorm", "Normalize to −14 LUFS")}</div>
          <div>${tg("audio.remove_fillers", "Cut “um / uh”")}${tg("audio.remove_pauses", "Cut long pauses")}${tg("audio.remove_retakes", "Cut repeated takes")}</div></div>
        <div><label class="f">Keep pauses up to ${a.pause_max}s</label><input type="range" min="0.15" max="1" step="0.05" data-p="audio.pause_max" data-num value="${a.pause_max}"></div>
        <div class="grid3"><div><label class="f">Music</label><select data-p="audio.music">${opt("", "None", a.music)}${m.assets.music.map(x => opt(x.path, x.name, a.music)).join("")}</select></div>
          <div><label class="f">Music volume</label><input type="range" min="0" max="0.6" step="0.01" data-p="audio.music_volume" data-num value="${a.music_volume}"></div><div>${tg("audio.duck", "Duck under speech", a.duck)}</div></div>
        <div class="grid2"><div>${tg("audio.sfx", "Sound effects on edits")}</div><div><label class="f">SFX volume</label><input type="range" min="0" max="1" step="0.05" data-p="audio.sfx_volume" data-num value="${a.sfx_volume}"></div></div>
        <h3 style="margin-top:8px">AI voice-over</h3>
        <textarea data-p="audio.voiceover.text" placeholder="Narration text (leave empty for none)">${esc(a.voiceover.text || "")}</textarea>
        <div class="grid3"><div><label class="f">Voice</label><select data-p="audio.voiceover.voice">${m.voices.map(v => opt(v, v, a.voiceover.voice || "af_heart")).join("")}</select></div>
          <div><label class="f">Language</label><select data-p="audio.voiceover.lang">${[["en-us", "English (US)"], ["en-gb", "English (UK)"], ["es", "Spanish"], ["fr-fr", "French"], ["it", "Italian"], ["pt-br", "Portuguese (BR)"], ["hi", "Hindi"], ["ja", "Japanese"], ["cmn", "Chinese"]].map(([v, l]) => opt(v, l, a.voiceover.lang)).join("")}</select></div>
          <div><label class="f">Mix</label><select data-p="audio.voiceover.mix">${opt("over", "Over original (ducked)", a.voiceover.mix)}${opt("replace", "Replace original", a.voiceover.mix)}</select></div></div>
        <button class="btn sm" id="vscript">✨ Write a script from this clip</button>
        ${ED.rules?.allows_ai_voice === "no" ? `<div class="pill warn">This campaign bans AI voices; it will be removed on render.</div>` : ""}`,
      visuals: `<div><label class="f">Layers on this clip</label>
        <div class="layer-stack">${[
          ["Captions", !!(s.captions?.length), s.captions?.length ? `${s.captions.length} lines` : "none"],
          ["Zooms", s.zooms.length > 0, s.zooms.length ? `${s.zooms.length} zoom${s.zooms.length === 1 ? "" : "s"}` : "none"],
          ["B-roll", s.broll.length > 0, s.broll.length ? `${s.broll.length} cutaway${s.broll.length === 1 ? "" : "s"}` : "none"],
          ["Logo", !!s.logo.path, s.logo.path ? s.logo.pos : "off"],
          ["Progress bar", !!s.frame?.progress, s.frame?.progress ? "on" : "off"],
          ["Punch-ins", s.transitions === "punch", s.transitions === "punch" ? "on jump cuts" : "off"],
        ].map(([label, active, reading]) => `<div class="layer-sheet${active ? " active" : ""}"><div class="lhead"><span class="dot ${active ? "done" : ""}"></span>${label}<span class="lmeter">${esc(reading)}</span></div></div>`).join("")}</div></div>
        <div><label class="f">Reel frame</label>${framePicker(m.frames, s.frame?.style || "none", 'data-p="frame.style"')}</div>
        <div class="grid3"><div><label class="f">Transition between sections</label><select data-p="join.type">${[["cut", "Hard cut"], ["dissolve", "Smooth crossfade"], ["flash", "Flash"], ["zoom", "Zoom punch"]].map(([v, l]) => opt(v, l, s.join?.type || "cut")).join("")}</select></div>
          <div style="grid-column:span 2"><label class="f">Frame headline (blank = clip title; *word* = highlighted)</label><input type="text" data-p="frame.text" value="${esc(s.frame?.text || "")}" placeholder="${esc(s.title?.text || "")}"></div></div>
        <div class="grid2"><div>${tg("frame.progress", "Progress bar", !!s.frame?.progress)}</div><div></div></div>
        <div class="grid2"><div>${tg("sharpen", "Sharpen", s.sharpen)}${tg("transitions", "Punch-in on jump cuts", s.transitions === "punch")}</div>
          <div><button class="btn sm" id="autoz">Auto zoom on key words</button> <button class="btn sm" id="clrz">Clear zooms (${s.zooms.length})</button></div></div>
        <div class="grid2"><div><label class="f">Fade-out at the end: ${(+(s.ending?.fade ?? 0)).toFixed(2)}s</label><input type="range" min="0" max="3" step="0.05" data-p="ending.fade" data-num value="${s.ending?.fade ?? 0}"></div>
          <div><label class="f">Fade to black: ${(+(s.ending?.to_black ?? 0)).toFixed(2)}s</label><input type="range" min="0" max="2" step="0.05" data-p="ending.to_black" data-num value="${s.ending?.to_black ?? 0}"></div></div>
        <div class="grid3"><div><label class="f">Logo</label><select data-p="logo.path">${opt("", "None", s.logo.path)}${m.assets.logo.map(x => opt(x.path, x.name, s.logo.path)).join("")}</select></div>
          <div><label class="f">Logo position</label><select data-p="logo.pos">${["top-right", "top-left", "bottom-right", "bottom-left"].map(p => opt(p, p, s.logo.pos)).join("")}</select></div>
          <div><label class="f">Logo size</label><input type="range" min="0.06" max="0.35" step="0.01" data-p="logo.scale" data-num value="${s.logo.scale}"></div></div>
        <div class="grid2"><div><label class="f">Intro</label><select data-p="intro">${opt("", "None", s.intro)}${m.assets.intro.map(x => opt(x.path, x.name, s.intro)).join("")}</select></div>
          <div><label class="f">Outro</label><select data-p="outro">${opt("", "None", s.outro)}${m.assets.intro.map(x => opt(x.path, x.name, s.outro)).join("")}</select></div></div>
        <h3 style="margin-top:8px">B-roll (${s.broll.length})</h3>
        <div class="results">${s.broll.map((b, i) => `<div class="result"><span class="ts">${fmt(b.t)} · ${b.dur}s</span><div class="txt">${esc(b.query || "")}<div class="hint">${esc(b.source || b.path)}</div></div><button class="btn sm danger" data-rb="${i}">✕</button></div>`).join("") || `<div class="hint">None. “New broll” (left) finds cutaways from your library${m.settings.pexels_key ? " and Pexels" : ""}.</div>`}</div>
        ${ED.rules?.allows_outside_footage === "no" ? `<div class="pill warn">This campaign only allows official footage; B-roll will be removed on render.</div>` : ""}`,
      post: `<div><label class="f">Caption</label><textarea data-p="meta.post.caption" style="min-height:110px">${esc(P.caption || "")}</textarea></div>
        <div class="grid2"><div><label class="f">Disclosure tag</label><input type="text" data-p="meta.post.disclosure" value="${esc(P.disclosure || "")}"></div>
          <div><label class="f">Hashtags (space separated)</label><input type="text" data-p="meta.post.hashtags" data-tags value="${esc((P.hashtags || []).join(" "))}"></div></div>
        <button class="btn sm" id="cpost">Copy full post</button>`,
      score: s.meta.score_parts ? `<div class="row"><span style="font-size:30px;font-weight:800;color:var(--accent-2)">${s.meta.score}</span><span class="hint grow">${esc(s.meta.score_parts.note)}</span><button class="btn sm" id="rescore">Re-score this cut</button></div>
        <div class="parts" style="margin-top:12px">${Object.values(s.meta.score_parts.parts).map(p => `<div class="p"><span>${esc(p.label)} <span class="hint">×${p.weight}</span></span><b>${p.score}</b><div class="bar" style="grid-column:1/-1"><i style="width:${p.score}%"></i></div><span class="ev">${esc(p.evidence)}</span></div>`).join("")}</div>`
        : `<div class="hint">No score for this clip.</div> <button class="btn sm" id="rescore">Score it</button>`,
    };
    $("#panel").innerHTML = `<div class="stack">${panels[ED.tab]}</div>`;
    function tg(path, label, on) { const v = on ?? getP(path); return `<label class="toggle"><input type="checkbox" data-p="${path}" ${v ? "checked" : ""}><span>${label}</span></label>`; }
    $$("#panel [data-p]").forEach(el => el.addEventListener(el.type === "range" || el.type === "color" ? "input" : "change", () => {
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (el.dataset.num !== undefined) v = +v;
      if (el.dataset.list !== undefined) v = v.split(",").map(x => x.trim()).filter(Boolean);
      if (el.dataset.tags !== undefined) v = v.split(/\s+/).filter(Boolean).map(t => t.startsWith("#") ? t : "#" + t);
      if (el.dataset.p === "transitions") v = v ? "punch" : "none";
      setP(el.dataset.p, v);
      if (el.dataset.p === "captions.offset") $("#offv").textContent = `${(+v).toFixed(2)}s`;
      change(`${el.dataset.p.split(".").pop()} → ${typeof v === "string" ? v.slice(0, 30) : JSON.stringify(v)}`, el.type === "range" || el.type === "color");
    }));
    $("#tx")?.addEventListener("change", e => { ED.spec.layout.target_x = +e.target.value; ED.spec.layout.mode = ED.spec.layout.mode === "auto" ? "track" : ED.spec.layout.mode; change("Follow a chosen person"); drawPanel(); });
    $("#txclr")?.addEventListener("click", () => { ED.spec.layout.target_x = null; change("Follow whoever is talking"); drawPanel(); });
    $("#newhook")?.addEventListener("click", async e => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Writing…";
      try { await saveSpec(); const r = await api(`/api/jobs/${ED.job}/clips/${ED.name}/regenerate`, { method: "POST", body: { target: "title" } });
            await reload(); toast(r.message, 4000); }
      catch (err) { toast(err.message); b.disabled = false; b.textContent = "✨ New hook"; }
    });
    $$('[data-p="title.whole"]').forEach(el => el.addEventListener("change", () => { ED.spec.title.seconds = el.checked ? 0 : 4; delete ED.spec.title.whole; }));
    $("#cscript")?.addEventListener("change", async e => {
      ED.spec.captions.script = e.target.value; ED.scriptSwap = true;
      toast(e.target.value === "roman" ? "Writing Hinglish captions…" : "Switching to the original script…");
      try { await refreshWords(); } finally { ED.scriptSwap = false; }
      change(e.target.value === "roman" ? "Captions in Roman letters" : "Captions in original script");
    });
    $("#fc")?.addEventListener("change", e => { const v = e.target.value.split(",").map(Number).filter(x => !isNaN(x)); ED.spec.layout.facecam = v.length === 4 ? v : null; change("Facecam box"); });
    $("#autoz")?.addEventListener("click", () => runAI("add zooms on the important words"));
    $("#clrz")?.addEventListener("click", () => { ED.spec.zooms = []; change("Cleared zooms"); drawPanel(); });
    $$("[data-rb]").forEach(b => b.onclick = () => { ED.spec.broll.splice(+b.dataset.rb, 1); change("Removed a B-roll insert"); drawPanel(); });
    $("#vscript")?.addEventListener("click", async e => { e.target.textContent = "Writing…"; const r = await api(`/api/jobs/${ED.job}/clips/${ED.name}/voice-script`, { method: "POST", body: {} }); ED.spec.audio.voiceover.text = r.script; change("AI wrote a voice-over script"); drawPanel(); });
    $("#cpost")?.addEventListener("click", () => { const p = ED.spec.meta.post || {}; navigator.clipboard.writeText([p.caption, p.disclosure, (p.hashtags || []).join(" ")].filter(Boolean).join("\n")); toast("Post copied"); });
    $("#rescore")?.addEventListener("click", async e => { await saveSpec(); e.target.textContent = "Scoring…"; await api(`/api/jobs/${ED.job}/clips/${ED.name}/rescore`, { method: "POST" }); await reload(); });
  }
  function getP(path) { return path.split(".").reduce((o, k) => o?.[k], ED.spec); }
  function setP(path, v) { const ks = path.split("."), last = ks.pop(); const o = ks.reduce((o, k) => (o[k] ||= {}), ED.spec); o[last] = v; }

  /* ---------- history / saving ---------- */
  function drawHistory() {
    const cur = ED.spec.v;
    $("#hist").innerHTML = [...ED.versions].reverse().map(v => `<div class="h"><span class="kbd">v${v.v}</span><span class="a">${esc(v.by === "ai" ? "🤖 " : "")}${esc(v.action)}</span>
      ${v.v === cur ? `<span class="pill ok">current</span>` : `<button class="btn sm" data-rs="${v.v}">Restore</button>`}</div>`).join("");
    $$("[data-rs]").forEach(b => b.onclick = () => restore(+b.dataset.rs));
  }
  function change(action, quiet) { ED.dirty = true; if (!ED.actions.includes(action)) ED.actions.push(action); if (!quiet) markDirty(); else markDirty(); $("#dur") && ($("#dur").textContent = `${duration(ED.spec).toFixed(1)}s`); }
  function markDirty() { const d = $("#dirty"); if (d) d.textContent = ED.dirty ? `● ${ED.actions.length} unsaved change(s)` : ""; }
  async function saveSpec(label) {
    if (!ED.dirty) return;
    const action = label || ED.actions.slice(0, 4).join("; ") + (ED.actions.length > 4 ? ` +${ED.actions.length - 4} more` : "");
    const r = await api(`/api/jobs/${ED.job}/clips/${ED.name}`, { method: "PUT", body: { spec: ED.spec, action } });
    ED.dirty = false; ED.actions = []; ED.spec.v = r.v;
    ED.versions = (await api(`/api/jobs/${ED.job}/clips/${ED.name}`)).versions;
    if ($("#hist")) drawHistory();
    markDirty();
  }
  async function reload() {
    const clip = await api(`/api/jobs/${ED.job}/clips/${ED.name}`);
    Object.assign(ED, { spec: clip.spec, versions: clip.versions, dirty: false, actions: [] });
    const t = $("#vid")?.currentTime || 0; render(); if ($("#vid")) $("#vid").currentTime = t;
  }
  async function restore(v) { if (ED.dirty && !confirm("Discard unsaved changes?")) return; await api(`/api/jobs/${ED.job}/clips/${ED.name}/restore`, { method: "POST", body: { v } }); await reload(); toast(`Restored version ${v}`); }
  function undo() { const vs = ED.versions.map(x => x.v); const i = vs.indexOf(ED.spec.v); if (ED.dirty) return reload(); if (i > 0) restore(vs[i - 1]); }
  function redo() { const log = ED.versions; const cur = log.find(x => x.v === ED.spec.v); const m = cur && /restored version (\d+)/.exec(cur.action); if (m) { const nxt = log.find(x => x.v > +m[1] && x.v < cur.v); if (nxt) restore(nxt.v); } }

  async function doRender(preview) {
    await saveSpec();
    await api(`/api/jobs/${ED.job}/clips/${ED.name}/render`, { method: "POST", body: { preview } });
    ED.lastRender = preview ? "preview" : "final";
    pollStatus();
  }
  async function pollStatus() {
    clearTimeout(statusTimer);
    const st = await api(`/api/jobs/${ED.job}/clips/${ED.name}/status`).catch(() => ({}));
    const el = $("#rstat"); if (!el) return;
    if (st.state === "queued" || st.state === "rendering") { el.innerHTML = `<span class="dot rendering"></span> ${st.preview ? "Rendering preview" : "Exporting full quality"}…`; statusTimer = setTimeout(pollStatus, 1500); }
    else if (st.state === "done" && ED.waiting !== st.finished) {
      ED.waiting = st.finished; ED.bust = Date.now(); ED.hasPreview = ED.hasPreview || st.preview; ED.lastRender = st.preview ? "preview" : "final";
      $("#vid").src = videoSrc(); el.innerHTML = st.preview ? "Preview ready (low resolution)." : `Exported. <a href="/files/${ED.job}/${ED.name}.mp4" download>Download</a>`;
    } else if (st.state === "failed") el.innerHTML = `<span class="pill bad">Render failed</span> <span class="hint">${esc((st.error || "").split("\n").slice(-2).join(" "))}</span>`;
  }

  async function runAI(cmd) {
    cmd = (cmd ?? $("#ai").value).trim(); if (!cmd) return;
    await saveSpec();
    $("#aires").textContent = "Thinking…";
    try {
      const r = await api(`/api/jobs/${ED.job}/clips/${ED.name}/ai`, { method: "POST", body: { command: cmd } });
      await reload();
      $("#aires").innerHTML = r.changed ? `✓ ${r.done.map(esc).join("<br>✓ ")}${r.warnings.length ? `<br><span class="pill warn">${esc(r.warnings.join(" "))}</span>` : ""}<br><span class="hint">Undo with ⌘Z. Hit “Save &amp; preview” to see it.</span>`
        : `No change: ${esc(r.explanation)}`;
    } catch (e) { $("#aires").textContent = e.message; }
  }

  /* ---------- events ---------- */
  function bind() {
    const vid = $("#vid");
    vid.ontimeupdate = () => { const src = outToSrc(ED.spec, vid.currentTime); const ph = $("#ph"); if (src != null && ph) { const [lo, hi] = ED.view; ph.style.left = `${((src - lo) / (hi - lo)) * 100}%`; }
      $$("#words span").forEach(sp => { const w = ED.spec.words[+sp.dataset.i]; sp.classList.toggle("cur", src != null && w && src >= w.s && src <= w.e); }); };
    $("#prev").onclick = () => doRender(true);
    $("#exp").onclick = () => doRender(false);
    $("#undo").onclick = undo; $("#redo").onclick = redo;
    $("#aigo").onclick = () => runAI(); $("#ai").onkeydown = e => e.key === "Enter" && runAI();
    $$("[data-regen]").forEach(b => b.onclick = async () => { await saveSpec(); b.disabled = true; const t = b.textContent; b.textContent = "…";
      try { const r = await api(`/api/jobs/${ED.job}/clips/${ED.name}/regenerate`, { method: "POST", body: { target: b.dataset.regen } }); await reload(); toast(r.message, 4000); } catch (e) { toast(e.message); b.disabled = false; b.textContent = t; } });
    $("#ptabs").onclick = e => { const t = e.target.dataset.t; if (!t) return; ED.tab = t; $$("#ptabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); drawPanel(); };
    $("#wm").onclick = e => { const k = e.target.dataset.wm; if (!k) return; ED.wordMode = k; $$("#wm button").forEach(b => b.classList.toggle("on", b.dataset.wm === k)); };
    $("#words").onclick = e => { const i = e.target.closest("span[data-i]")?.dataset.i; if (i != null) onWord(+i); };
    $("#addsec").onclick = () => {
      const s = prompt("Add a section from the source video.\nStart (mm:ss or seconds):", fmt(ED.spec.segments.at(-1).end)); if (s === null) return;
      const e = prompt("End (mm:ss or seconds):", fmt(parseTime(s) + 5)); if (e === null) return;
      const a = parseTime(s), b = parseTime(e); if (b - a < 0.5) return toast("Section must be at least 0.5 s");
      const first = confirm("Put it at the START of the clip (e.g. as a hook)?\nOK = start, Cancel = end");
      ED.spec.segments[first ? "unshift" : "push"]({ start: a, end: b }); refreshWords(); drawTimeline(); drawSegments(); change(`Added section ${fmt(a)}–${fmt(b)}`);
    };
    keyHandler = e => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); doRender(true); }
      else if (e.key === " ") { e.preventDefault(); vid.paused ? vid.play() : vid.pause(); }
      else if (e.key === "j") vid.currentTime = Math.max(0, vid.currentTime - 2);
      else if (e.key === "l") vid.currentTime += 2;
      else if (e.key === "k") vid.pause();
      else if (e.key === "i" || e.key === "o") {
        const src = outToSrc(ED.spec, vid.currentTime); if (src == null) return;
        if (e.key === "i") ED.spec.segments[0].start = +src.toFixed(2); else ED.spec.segments.at(-1).end = +src.toFixed(2);
        refreshWords(); drawTimeline(); drawSegments(); change(e.key === "i" ? "Set start at playhead" : "Set end at playhead");
      }
    };
    document.addEventListener("keydown", keyHandler);
  }
})();
