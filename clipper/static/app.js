/* Clipper web app: router + views (the clip editor lives in editor.js) */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
let META = null, pollTimer = null;

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.body = JSON.stringify(o.body); o.headers = { "Content-Type": "application/json" }; }
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Request failed (${r.status})`);
  return j;
}
function toast(msg, ms = 2600) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}
function fmt(t) { t = Math.max(0, +t || 0); const m = Math.floor(t / 60), s = (t % 60).toFixed(1); return `${m}:${s.padStart(4, "0")}`; }
function parseTime(v) { if (!v) return 0; if (String(v).includes(":")) { const [m, s] = v.split(":"); return (+m) * 60 + (+s); } return +v || 0; }
async function loadMeta() { META = await api("/api/meta"); return META; }
function uploadFile(kind, file, onProgress) {
  return new Promise((res, rej) => {
    const fd = new FormData(); fd.append("file", file);
    const x = new XMLHttpRequest(); x.open("POST", `/api/upload/${kind}`);
    x.upload.onprogress = e => onProgress && e.total && onProgress(e.loaded / e.total);
    x.onload = () => { const j = JSON.parse(x.responseText || "{}"); x.status < 300 ? res(j) : rej(new Error(j.detail || "Upload failed")); };
    x.onerror = () => rej(new Error("Upload failed")); x.send(fd);
  });
}
const opt = (v, label, sel) => `<option value="${esc(v)}" ${v === sel ? "selected" : ""}>${esc(label ?? v)}</option>`;
const toggle = (name, label, on, hint = "") =>
  `<label class="toggle"><input type="checkbox" name="${name}" ${on ? "checked" : ""}><span>${label}${hint ? `<small>${hint}</small>` : ""}</span></label>`;

/* ---------------- router ---------------- */
const routes = {
  "": viewNew, projects: viewProjects, job: viewJob, edit: (...a) => window.viewEditor(...a),
  brands: viewBrands, calendar: viewCalendar, analytics: viewAnalytics, settings: viewSettings, tools: viewTools,
};
async function route() {
  clearTimeout(pollTimer);
  window.editorCleanup && window.editorCleanup();
  const [name, ...args] = location.hash.replace(/^#\/?/, "").split("/");
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.r === (name || "home") || (name === "job" && a.dataset.r === "projects")));
  view.innerHTML = `<div class="empty">Loading…</div>`;
  try { await (routes[name] || viewNew)(...args.map(decodeURIComponent)); }
  catch (e) { view.innerHTML = `<div class="empty">Something went wrong: ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);

/* ---------------- new job ---------------- */
async function viewNew() {
  const m = await loadMeta();
  const s = m.settings;
  view.innerHTML = `
  <h1>New clips</h1><p class="sub">Video + campaign brief in, ready-to-post clips out. Everything runs on this Mac.</p>
  <form class="newjob" id="f" autocomplete="off">
    <div class="stack">
      <div class="card stack">
        <h2>1 · Video</h2>
        <label class="drop" id="drop"><strong id="vname">Drop a video or click to upload</strong>MP4, MOV, MKV, WebM<input type="file" accept="video/*,.mkv"></label>
        <div class="bar" id="upbar" hidden><i style="width:0"></i></div>
        <div><label class="f">…or a path on this Mac (fastest for big files)</label><input type="text" name="video_path" placeholder="/Users/you/Downloads/episode.mp4"></div>
        <div><label class="f">…or a link (YouTube, Drive, Twitch, X, Vimeo, Dropbox, direct file)</label><input type="url" name="video_url" placeholder="https://"></div>
      </div>
      <div class="card stack">
        <h2>2 · Campaign brief</h2>
        <div class="tabs" id="btabs"><button type="button" data-t="url" class="on">Google Doc</button><button type="button" data-t="file">PDF / file</button><button type="button" data-t="text">Paste</button><button type="button" data-t="none">None</button></div>
        <div data-p="url"><input type="url" name="brief_url" placeholder="https://docs.google.com/document/d/…"></div>
        <div data-p="file" hidden><label class="drop"><strong id="bname">Choose the brief</strong>.pdf, .txt or .md<input type="file" accept=".pdf,.txt,.md" id="bfile"></label></div>
        <div data-p="text" hidden><textarea name="brief_text" placeholder="Paste every rule from the campaign…"></textarea></div>
        <div data-p="none" hidden class="hint">No rules: clips are picked purely on quality, default 5 clips of 20–60 s.</div>
      </div>
      <div class="card stack">
        <h2>3 · What to clip</h2>
        <div class="tabs" id="mode"><button type="button" data-m="clips" class="on">Find clips</button><button type="button" data-m="captions">Captions only</button></div>
        <div><label class="f">AI Copilot: tell it what to look for (optional)</label><textarea name="prompt" style="min-height:64px" placeholder="e.g. moments where they talk about pricing, funny reactions, the DMZ early access announcement"></textarea></div>
        <div class="grid2" data-clipsonly>
          <div><label class="f">Number of clips</label><input type="number" name="clips" min="0" max="30" placeholder="From brief"></div>
          <div><label class="f">Length of each clip</label><select name="len">${opt("", "From brief")}${["15", "30", "45", "60", "90"].map(v => opt(v, `~${v}s`)).join("")}${opt("custom", "Custom range")}${opt("long", "Long (2–15 min)")}</select></div>
        </div>
        <div data-clipsonly><label class="f">What kind of video is it?</label>
          <div class="mode-grid"><input type="hidden" name="mode" value="auto">
            ${[["auto", {label: "Auto-detect", hint: "Clipper listens and decides"}], ...Object.entries(m.modes)].map(([k, v]) =>
              `<button type="button" class="mode ${k === "auto" ? "on" : ""}" data-mode="${k}"><b>${esc(v.label)}</b><span>${esc(v.hint)}</span></button>`).join("")}
          </div></div>
        <div class="grid2" id="custom" hidden><div><label class="f">Min seconds</label><input type="number" name="min" min="3"></div><div><label class="f">Max seconds</label><input type="number" name="max" min="3" max="900"></div></div>
        <div class="grid2"><div><label class="f">Only from (mm:ss)</label><input type="text" name="start" placeholder="start"></div><div><label class="f">Up to (mm:ss)</label><input type="text" name="end" placeholder="end"></div></div>
      </div>
    </div>
    <div class="stack">
      <div class="card stack">
        <h2>Look</h2>
        <div class="grid3">
          <div><label class="f">Brand kit</label><select name="brand">${opt("", "Default")}${opt("none", "None")}${m.brands.map(b => opt(b.name, b.name, s.default_brand)).join("")}</select></div>
          <div><label class="f">Aspect ratio</label><select name="aspect">${m.aspects.map(a => opt(a, a === "original" ? "Original" : a, "9:16")).join("")}</select></div>
          <div><label class="f">Transition between moments</label><select name="join">${[["cut", "Hard cut"], ["dissolve", "Smooth crossfade"], ["flash", "Flash"], ["zoom", "Zoom punch"]].map(([v, l]) => opt(v, l, "dissolve")).join("")}</select></div>
          <div><label class="f">Caption script</label><select name="script">${[["auto", "Auto (Hinglish for Hindi)"], ["roman", "Roman letters (Hinglish)"], ["native", "Original script (Devanagari)"]].map(([v, l]) => opt(v, l, "auto")).join("")}</select></div>
          <div><label class="f">Framing</label><select name="layout">${[["auto", "Auto (per shot)"], ["track", "Follow speaker"], ["split", "Split screen (2 people)"], ["gameplay", "Gameplay + facecam"], ["screen", "Screen share + speaker"], ["fit", "Full frame + blur"], ["center", "Center crop"]].map(([v, l]) => opt(v, l, "auto")).join("")}</select></div>
        </div>
        <div><label class="f">Reel frame</label>${framePicker(m.frames, "none", 'name="frame"')}</div>
        <div><label class="f">Caption style</label><div class="tpl-grid" id="tpls">${Object.entries(m.templates).map(([k, l], i) => `<div class="tpl ${i === 0 ? "on" : ""}" data-k="${k}"><b style="${tplStyle(k)}">${esc(l)}</b></div>`).join("")}</div></div>
        <div class="grid2">
          <div>${toggle("hook", "On-screen hook", true, "a relatable line, shown the whole clip")}${toggle("keywords", "Highlight key words", true)}${toggle("emoji", "Emoji", false)}${toggle("auto_zoom", "Zoom on key moments", true)}${toggle("punch", "Punch-in on jump cuts", false)}${toggle("progress", "Progress bar", false)}</div>
          <div>${toggle("broll", "B-roll cutaways", false, s.pexels_key ? "Your library + Pexels" : "Your library (add Pexels key in Settings)")}${toggle("censor", "Censor profanity", false)}${toggle("sharpen", "Sharpen", false)}${toggle("speaker_colors", "Colour per speaker", false)}</div>
        </div>
      </div>
      <div class="card stack">
        <h2>Audio</h2>
        <div class="grid2">
          <div>${toggle("enhance", "Enhance speech", false, "EQ + compression")}${toggle("denoise", "Remove background noise", false, "RNNoise")}${toggle("remove_fillers", "Cut “um / uh”", true, "talking clips")}</div>
          <div>${toggle("remove_pauses", "Cut long pauses", true, "measured from the audio")}${toggle("remove_retakes", "Cut repeated takes", false)}${toggle("sfx", "Sound effects", false, "whoosh / pop on edits")}</div>
        </div>
        <div class="grid2"><div><label class="f">Background music</label><select name="music">${opt("", "None")}${m.assets.music.map(a => opt(a.path, a.name)).join("")}</select></div>
        <div><label class="f">Music volume</label><input type="range" name="music_volume" min="0" max="0.5" step="0.01" value="0.12"></div></div>
        <div class="hint">Anything a brief bans (music, AI voices, outside footage, logos) is switched off automatically with a warning.</div>
      </div>
      <button class="btn primary" id="go" type="submit" style="padding:14px">Generate clips</button>
      <div class="err" id="err"></div>
      ${m.busy ? `<div class="hint">A job is running now; new jobs wait until it finishes.</div>` : ""}
    </div>
  </form>`;
  let videoUpload = null, briefTab = "url", mode = "clips", briefPath = null, tpl = Object.keys(m.templates)[0];
  const f = $("#f");
  $("#btabs").onclick = e => { const t = e.target.dataset.t; if (!t) return; briefTab = t; $$("#btabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); $$("[data-p]").forEach(p => p.hidden = p.dataset.p !== t); };
  $("#mode").onclick = e => { const t = e.target.dataset.m; if (!t) return; mode = t; $$("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === t)); $$("[data-clipsonly]").forEach(x => x.hidden = t !== "clips"); };
  $("#tpls").onclick = e => { const k = e.target.closest(".tpl")?.dataset.k; if (!k) return; tpl = k; $$(".tpl").forEach(x => x.classList.toggle("on", x.dataset.k === k)); };
  f.len.onchange = () => $("#custom").hidden = f.len.value !== "custom";
  const vin = $("#drop input");
  const doUpload = async file => {
    $("#vname").textContent = file.name; $("#upbar").hidden = false;
    try { videoUpload = await uploadFile("video", file, p => $("#upbar i").style.width = `${p * 100}%`); $("#vname").textContent = `✓ ${file.name}`; }
    catch (e) { $("#err").textContent = e.message; videoUpload = null; }
  };
  vin.onchange = () => vin.files[0] && doUpload(vin.files[0]);
  const drop = $("#drop");
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", e => { e.preventDefault(); e.dataTransfer.files[0] && doUpload(e.dataTransfer.files[0]); });
  $("#bfile").onchange = async e => { const file = e.target.files[0]; if (!file) return; $("#bname").textContent = file.name; briefPath = (await uploadFile("brief", file)).path; $("#bname").textContent = `✓ ${file.name}`; };

  f.onsubmit = async e => {
    e.preventDefault(); $("#err").textContent = "";
    const on = n => f[n].checked;
    const settings = {
      prompt: f.prompt.value.trim(), template: tpl, mode: f.mode.value === "auto" ? undefined : f.mode.value, aspect: f.aspect.value, layout: f.layout.value, brand: f.brand.value || undefined,
      hook: on("hook"), frame: { style: f.frame.value, progress: on("progress") }, join: { type: f.join.value, dur: 0.2 },
      auto_zoom: on("auto_zoom"), transitions: on("punch") ? "punch" : "none", sharpen: on("sharpen"), broll: on("broll"),
      captions: { script: f.script.value, template: tpl, keywords: on("keywords"), emoji: on("emoji"), censor: on("censor"), speaker_colors: on("speaker_colors") },
      audio: { enhance: on("enhance"), denoise: on("denoise"), remove_fillers: on("remove_fillers"), remove_pauses: on("remove_pauses"),
               remove_retakes: on("remove_retakes"), sfx: on("sfx"), music: f.music.value, music_volume: +f.music_volume.value },
    };
    if (f.start.value) settings.start = parseTime(f.start.value);
    if (f.end.value) settings.end = parseTime(f.end.value);
    if (mode === "captions") settings.caption_only = true;
    else {
      if (+f.clips.value) settings.clips = +f.clips.value;
      const L = f.len.value;
      if (L === "custom") { if (!+f.min.value || !+f.max.value || +f.min.value > +f.max.value) { $("#err").textContent = "Set a custom min and max (min ≤ max)."; return; } settings.min = +f.min.value; settings.max = +f.max.value; }
      else if (L === "long") { settings.min = 120; settings.max = 900; }
      else if (L) { settings.min = Math.max(5, +L - 5); settings.max = +L + 5; }
    }
    const brief = briefTab === "url" && f.brief_url.value.trim() ? { url: f.brief_url.value.trim() }
      : briefTab === "text" && f.brief_text.value.trim() ? { text: f.brief_text.value }
      : briefTab === "file" && briefPath ? { path: briefPath } : null;
    const body = { video_path: videoUpload?.path || f.video_path.value.trim(), video_url: videoUpload ? "" : f.video_url.value.trim(), brief, settings };
    $("#go").disabled = true; $("#go").textContent = "Starting…";
    try { const j = await api("/api/jobs", { method: "POST", body }); location.hash = `#/job/${j.id}`; }
    catch (x) { $("#err").textContent = x.message; $("#go").disabled = false; $("#go").textContent = "Generate clips"; }
  };
}
function tplStyle(k) {
  return ({ bold_pop: "color:#FFE600;-webkit-text-stroke:1px #000", karaoke: "color:#2DFF5A", boxed: "color:#fff;background:#111",
            minimal: "color:#fff;font-weight:600", neon: "color:#00E5FF;text-shadow:0 0 8px #00E5FF", word_pop: "color:#fff;font-size:16px",
            subtitle: "color:#fff;font-weight:500" })[k] || "";
}

/* ---------------- projects ---------------- */
async function viewProjects() {
  const jobs = await api("/api/jobs");
  view.innerHTML = `<h1>Projects</h1><p class="sub">Every job, newest first.</p>
    <div class="joblist">${jobs.length ? jobs.map(j => `<a class="jobrow" href="#/job/${j.id}"><span class="dot ${j.status}"></span>
      <span class="name">${esc(j.video)}</span>${j.prompt ? `<span class="pill">${esc(j.prompt.slice(0, 40))}</span>` : ""}
      <span class="hint">${new Date(j.created * 1000).toLocaleString()}</span></a>`).join("") : `<div class="empty">No projects yet. <a href="#/">Make some clips</a>.</div>`}</div>`;
}

/* ---------------- job ---------------- */
async function viewJob(id) {
  const j = await api(`/api/jobs/${id}`);
  const r = j.rules;
  const ok = b => b ? "✅" : "❌";
  view.innerHTML = `
    <div class="row"><h1 class="grow" style="overflow-wrap:anywhere">${esc(j.video)}</h1>
      ${j.status === "running" ? `<span class="pill warn">Working…</span><button class="btn sm" id="cancel">Cancel</button>`
        : j.status === "done" ? `<span class="pill ok">Done · ${j.clips.length} clips</span>` : `<span class="pill bad">Failed</span>`}
      <button class="btn sm danger" id="del">Delete</button></div>
    ${j.prompt ? `<p class="sub">Prompt: “${esc(j.prompt)}”</p>` : `<p class="sub"></p>`}
    ${j.status !== "done" || !j.clips.length ? `<div class="log" id="log">${esc(j.log.join("\n")) || "Starting…"}</div>` : ""}
    ${r ? `<div class="card" style="margin-top:16px"><h2>Rules found in the brief</h2><div class="chips">${[
      `${r.num_clips} clips`, `${r.min_seconds}–${r.max_seconds}s`, r.disclosure_hashtag && `disclosure ${r.disclosure_hashtag}`,
      ...r.required_mentions, ...r.required_hashtags, r.max_hashtags && `max ${r.max_hashtags} hashtags`, r.language && `language ${r.language}`,
      r.allows_music === "no" && "no music", r.allows_ai_voice === "no" && "no AI voice", r.allows_outside_footage === "no" && "official footage only"]
      .filter(Boolean).map(c => `<span class="pill">${esc(c)}</span>`).join("")}</div></div>` : ""}
    ${j.status === "done" ? `<div class="grid2" style="margin-top:16px">
      <div class="card stack"><h2>Find a topic</h2><div class="row"><input type="text" id="q" class="grow" placeholder="e.g. pricing, the boss fight, early access"><button class="btn" id="qgo">Search</button></div><div class="results" id="qres"></div></div>
      <div class="card stack"><h2>Reprompt</h2><textarea id="rp" style="min-height:58px" placeholder="Different instructions for a new set of clips (same video, transcript reused)">${esc(j.settings.prompt || "")}</textarea>
        <div class="row"><input type="number" id="rpn" placeholder="clips" style="width:90px" value="${j.settings.clips || ""}"><button class="btn primary" id="rpgo">New clip set</button></div></div></div>` : ""}
    <div class="clips" style="margin-top:18px">${j.clips.map(c => clipCard(id, c, r)).join("")}</div>
    ${r && r.manual_checks?.length && j.status === "done" ? `<div class="card" style="margin-top:18px"><h2>Check these yourself before posting</h2><ul>${r.manual_checks.map(m => `<li class="hint">${esc(m)}</li>`).join("")}</ul></div>` : ""}`;
  const log = $("#log"); if (log) log.scrollTop = log.scrollHeight;
  $("#cancel")?.addEventListener("click", () => api(`/api/jobs/${id}/cancel`, { method: "POST" }));
  $("#del").onclick = async () => { if (!confirm("Delete this project and all its clips? This can't be undone.")) return; await api(`/api/jobs/${id}`, { method: "DELETE" }); location.hash = "#/projects"; };
  $$(".score", view).forEach(s => s.onclick = () => s.nextElementSibling.hidden = !s.nextElementSibling.hidden);
  $$("[data-copy]", view).forEach(b => b.onclick = async () => { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied ✓"; setTimeout(() => b.textContent = "Copy caption", 1400); });
  $$("[data-sched]", view).forEach(b => b.onclick = () => scheduleDialog(id, b.dataset.sched, b.dataset.cap));
  if ($("#qgo")) {
    const go = async () => {
      $("#qres").innerHTML = `<div class="hint">Searching…</div>`;
      const res = await api(`/api/jobs/${id}/search?q=${encodeURIComponent($("#q").value)}`);
      $("#qres").innerHTML = res.length ? res.map(h => `<div class="result"><span class="ts">${fmt(h.start)}–${fmt(h.end)}</span><div class="txt">${esc(h.text)}<div class="hint">${esc(h.why)}</div></div>
        <button class="btn sm" data-add="${h.start},${h.end}">Make clip</button></div>`).join("") : `<div class="hint">Nothing found.</div>`;
      $$("[data-add]").forEach(b => b.onclick = async () => { const [s, e] = b.dataset.add.split(",").map(Number); await api(`/api/jobs/${id}/clips`, { method: "POST", body: { start: Math.max(0, s - 0.3), end: e + 0.3 } }); b.textContent = "Making…"; b.disabled = true; toast("Making the clip — it appears below when ready."); setTimeout(route, 4000); });
    };
    $("#qgo").onclick = go; $("#q").onkeydown = e => e.key === "Enter" && go();
    $("#rpgo").onclick = async () => { try { const n = await api(`/api/jobs/${id}/reprompt`, { method: "POST", body: { prompt: $("#rp").value.trim(), ...(+$("#rpn").value ? { clips: +$("#rpn").value } : {}) } }); location.hash = `#/job/${n.id}`; } catch (e) { toast(e.message); } };
  }
  const anyRender = j.clips.some(c => c.render && ["queued", "rendering"].includes(c.render.state));
  if (j.status === "running" || anyRender) pollTimer = setTimeout(() => location.hash.startsWith(`#/job/${id}`) && viewJob(id), 2500);
}

function clipCard(id, c, r) {
  const ok = b => b ? "✅" : "❌";
  const sp = c.score_parts;
  const lenOk = r ? c.seconds >= r.min_seconds - 1 && c.seconds <= r.max_seconds + 1 : true;
  const tagsOk = r ? r.required_hashtags.every(t => c.hashtags.map(x => x.toLowerCase()).includes(t.toLowerCase())) : true;
  const mentOk = r ? r.required_mentions.every(m => (c.caption || "").toLowerCase().includes(m.toLowerCase())) : true;
  const name = c.file.replace(".mp4", "");
  return `<div class="clip">
    <video src="/files/${id}/${c.file}?v=${c.v || 0}" controls preload="metadata" playsinline></video>
    <div class="body">
      ${sp ? `<div class="score"><span class="num">${c.score}</span><div class="grow"><div class="bar"><i style="width:${c.score}%"></i></div><div class="hint">Quality estimate · tap for why</div></div></div>
      <div class="parts" hidden>${Object.values(sp.parts).map(p => `<div class="p"><span>${esc(p.label)} <span class="hint">×${p.weight}</span></span><b>${p.score}</b><div class="bar" style="grid-column:1/-1"><i style="width:${p.score}%"></i></div><span class="ev">${esc(p.evidence)}</span></div>`).join("")}<div class="hint">${esc(sp.note)}</div></div>` : ""}
      <div class="t">${esc(c.title || "Untitled clip")}</div>
      <div class="chips"><span class="pill">${c.seconds}s</span><span class="pill">${esc(c.aspect || "9:16")}</span><span class="pill">${esc(c.framing)}</span><span class="pill">${esc(c.picked_by || "")}</span></div>
      ${(c.warnings || []).map(w => `<div class="pill warn">${esc(w)}</div>`).join("")}
      ${c.post_text ? `<div class="post">${esc(c.post_text)}</div>` : ""}
      ${r ? `<ul class="checks"><li>${ok(lenOk)} Length within rules</li><li>${ok(tagsOk && mentOk)} Required tags &amp; hashtags</li>
        ${r.caption_must_mention?.length ? `<li>${ok(!c.missing_points.length)} Caption says: ${esc(r.caption_must_mention.join(", "))}</li>` : ""}
        <li>${ok(!c.banned_words_spoken.length)} No banned words spoken</li></ul>` : ""}
      <div class="actions">
        <a class="btn sm primary" href="#/edit/${id}/${name}">Edit</a>
        ${c.post_text ? `<button class="btn sm" data-copy="${esc(c.post_text)}">Copy caption</button>` : ""}
        <a class="btn sm" href="/files/${id}/${c.file}" download>Download</a>
        <button class="btn sm" data-sched="${name}" data-cap="${esc(c.post_text || "")}">Schedule</button>
      </div>
    </div></div>`;
}

function scheduleDialog(job, clip, caption) {
  const d = document.createElement("div");
  d.className = "toast"; d.style.cssText += "bottom:auto;top:20%;width:min(440px,92vw)";
  const soon = new Date(Date.now() + 3600e3); soon.setMinutes(0);
  const local = new Date(soon - soon.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
  d.innerHTML = `<div class="stack"><h3>Schedule ${esc(clip)}</h3>
    <div class="grid2"><div><label class="f">Platform</label><select id="pl">${["tiktok", "instagram", "youtube", "x", "facebook", "linkedin", "bluesky"].map(p => opt(p)).join("")}</select></div>
    <div><label class="f">When</label><input type="datetime-local" id="wh" value="${local}"></div></div>
    <div><label class="f">Caption</label><textarea id="cp">${esc(caption)}</textarea></div>
    <div class="row"><button class="btn primary" id="ok">Add to calendar</button><button class="btn" id="no">Cancel</button></div></div>`;
  document.body.appendChild(d);
  $("#no", d).onclick = () => d.remove();
  $("#ok", d).onclick = async () => { await api("/api/calendar", { method: "POST", body: { job, clip, platform: $("#pl", d).value, when: $("#wh", d).value, caption: $("#cp", d).value } }); d.remove(); toast("Added to the calendar"); };
}

/* ---------------- brand kits ---------------- */
async function viewBrands(edit) {
  const m = await loadMeta();
  const kits = m.brands;
  const k = kits.find(b => b.name === edit) || { name: "", captions: {}, logo: {}, audio: {} };
  const c = k.captions || {}, lg = k.logo || {};
  view.innerHTML = `<h1>Brand kits</h1><p class="sub">Reusable looks: caption style, fonts, colours, logo, intro/outro, music. Pick one when you make clips.</p>
  <div class="newjob">
    <div class="stack"><div class="joblist">${kits.map(b => `<a class="jobrow" href="#/brands/${encodeURIComponent(b.name)}"><span class="name">${esc(b.name)}</span>${b.name === m.settings.default_brand ? `<span class="pill ok">default</span>` : ""}</a>`).join("") || `<div class="hint">No brand kits yet.</div>`}</div>
      <a class="btn" href="#/brands">＋ New kit</a></div>
    <form class="card stack" id="bf">
      <div class="grid2"><div><label class="f">Name</label><input type="text" name="name" value="${esc(k.name)}" required></div>
      <div><label class="f">Caption template</label><select name="template">${Object.entries(m.templates).map(([v, l]) => opt(v, l, c.template || "bold_pop")).join("")}</select></div></div>
      <div class="grid3">
        <div><label class="f">Font</label><select name="font">${opt("", "Template default", c.font || "")}${["Arial Black", "Impact", "Futura", "Helvetica Neue", "Montserrat", "Avenir Next"].concat(m.assets.font.map(f => f.name.replace(/\.(ttf|otf|ttc)$/i, ""))).map(f => opt(f, f, c.font)).join("")}</select></div>
        <div><label class="f">Text colour</label><input type="color" name="color" value="${c.color || "#ffffff"}"></div>
        <div><label class="f">Highlight colour</label><input type="color" name="highlight" value="${c.highlight || "#ffe600"}"></div>
      </div>
      <div class="grid3">
        <div><label class="f">Caption position</label><select name="position">${["", "top", "middle", "lower", "bottom"].map(p => opt(p, p || "Template default", c.position || "")).join("")}</select></div>
        <div><label class="f">Words per line</label><input type="number" name="words_per_line" min="0" max="8" value="${c.words_per_line || ""}" placeholder="default"></div>
        <div><label class="f">Aspect</label><select name="aspect">${opt("", "Any")}${m.aspects.map(a => opt(a, a, k.aspect)).join("")}</select></div>
      </div>
      <div class="grid3">
        <div><label class="f">Logo</label><select name="logo">${opt("", "None")}${m.assets.logo.map(a => opt(a.path, a.name, lg.path)).join("")}</select></div>
        <div><label class="f">Logo position</label><select name="logo_pos">${["top-right", "top-left", "bottom-right", "bottom-left"].map(p => opt(p, p, lg.pos || "top-right")).join("")}</select></div>
        <div><label class="f">Logo size</label><input type="range" name="logo_scale" min="0.06" max="0.35" step="0.01" value="${lg.scale || 0.16}"></div>
      </div>
      <div class="grid3">
        <div><label class="f">Watermark text</label><input type="text" name="watermark" value="${esc(k.watermark || "")}" placeholder="@yourhandle"></div>
        <div><label class="f">Intro</label><select name="intro">${opt("", "None")}${m.assets.intro.map(a => opt(a.path, a.name, k.intro)).join("")}</select></div>
        <div><label class="f">Outro</label><select name="outro">${opt("", "None")}${m.assets.intro.map(a => opt(a.path, a.name, k.outro)).join("")}</select></div>
      </div>
      <div class="grid2"><div>${toggle("uppercase", "ALL CAPS captions", c.uppercase !== false)}${toggle("emoji", "Emoji", !!c.emoji)}${toggle("auto_zoom", "Auto zoom", k.auto_zoom !== false)}</div>
        <div>${toggle("punch", "Punch-in jump cuts", k.transitions === "punch")}${toggle("sharpen", "Sharpen", !!k.sharpen)}${toggle("is_default", "Use by default", m.settings.default_brand === k.name && !!k.name)}</div></div>
      <div class="hint">Upload fonts, logos and intro videos in Settings → Library.</div>
      <div class="row"><button class="btn primary">Save kit</button>${k.name ? `<button type="button" class="btn danger" id="bdel">Delete</button>` : ""}</div>
    </form></div>`;
  const f = $("#bf");
  f.onsubmit = async e => {
    e.preventDefault();
    const kit = { name: f.name.value.trim(), aspect: f.aspect.value, watermark: f.watermark.value, intro: f.intro.value, outro: f.outro.value,
      auto_zoom: f.auto_zoom.checked, transitions: f.punch.checked ? "punch" : "", sharpen: f.sharpen.checked,
      captions: { template: f.template.value, font: f.font.value, color: f.color.value, highlight: f.highlight.value, position: f.position.value,
                  words_per_line: +f.words_per_line.value || 0, uppercase: f.uppercase.checked, emoji: f.emoji.checked },
      logo: f.logo.value ? { path: f.logo.value, pos: f.logo_pos.value, scale: +f.logo_scale.value, opacity: 0.9, animate: true } : {} };
    await api("/api/brands", { method: "POST", body: kit });
    if (f.is_default.checked) await api("/api/settings", { method: "PUT", body: { default_brand: kit.name } });
    toast("Brand kit saved"); location.hash = `#/brands/${encodeURIComponent(kit.name)}`; route();
  };
  $("#bdel")?.addEventListener("click", async () => { if (!confirm(`Delete brand kit “${k.name}”?`)) return; await api(`/api/brands/${encodeURIComponent(k.name)}`, { method: "DELETE" }); location.hash = "#/brands"; });
}

/* ---------------- calendar ---------------- */
async function viewCalendar() {
  const c = await api("/api/calendar");
  const by = {};
  c.posts.forEach(p => (by[p.when.slice(0, 10)] ||= []).push(p));
  view.innerHTML = `<h1>Calendar</h1><p class="sub">Plan posts per platform. YouTube Shorts can upload (and schedule) directly${c.youtube.connected ? "" : " once you connect YouTube in Settings"}; TikTok and Instagram stay copy-and-post — you get a reminder here when they're due.</p>
    ${c.due.length ? `<div class="card" style="border-color:var(--warn)"><h2>Due now</h2>${c.due.map(p => `<div class="hint">${esc(p.platform)} · ${esc(p.clip)} · ${new Date(p.when).toLocaleString()}</div>`).join("")}</div>` : ""}
    <div class="stack" style="margin-top:16px">${Object.keys(by).sort().map(day => `<div class="card"><h2>${new Date(day + "T12:00").toDateString()}</h2>
      <table class="t"><tr><th>Time</th><th>Platform</th><th>Clip</th><th>Caption</th><th>Status</th><th></th></tr>${by[day].map(p => `<tr>
        <td>${new Date(p.when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td><td>${esc(p.platform)}</td>
        <td><a href="#/edit/${p.job}/${p.clip}">${esc(p.clip)}</a></td><td class="hint" style="max-width:320px">${esc(p.caption.slice(0, 120))}</td>
        <td><select data-st="${p.id}">${["planned", "posted", "uploaded", "skipped"].map(s => opt(s, s, p.status)).join("")}</select>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">link</a>` : ""}</td>
        <td class="row" style="flex-wrap:nowrap"><a class="btn sm" href="/files/${p.job}/${p.clip}.mp4" download>Video</a><button class="btn sm" data-cp="${esc(p.caption)}">Copy</button>
          ${p.platform === "youtube" && p.status === "planned" ? `<button class="btn sm primary" data-yt="${p.id}" ${c.youtube.connected ? "" : "disabled title='Connect YouTube in Settings'"}>Upload</button>` : ""}
          <button class="btn sm danger" data-del="${p.id}">✕</button></td></tr>`).join("")}</table></div>`).join("") || `<div class="empty">Nothing scheduled. Use “Schedule” on any clip.</div>`}</div>`;
  $$("[data-st]").forEach(s => s.onchange = () => api(`/api/calendar/${s.dataset.st}`, { method: "PUT", body: { status: s.value } }).then(() => toast("Updated")));
  $$("[data-cp]").forEach(b => b.onclick = () => navigator.clipboard.writeText(b.dataset.cp).then(() => toast("Caption copied")));
  $$("[data-del]").forEach(b => b.onclick = async () => { await api(`/api/calendar/${b.dataset.del}`, { method: "DELETE" }); route(); });
  $$("[data-yt]").forEach(b => b.onclick = async () => { b.disabled = true; b.textContent = "Uploading…"; try { const r = await api(`/api/calendar/${b.dataset.yt}/youtube`, { method: "POST", body: { schedule: true } }); toast(`Uploaded: ${r.url}`); route(); } catch (e) { toast(e.message); b.disabled = false; b.textContent = "Upload"; } });
}

/* ---------------- analytics ---------------- */
async function viewAnalytics() {
  const a = await api("/api/analytics");
  const yt = (await loadMeta()).youtube;
  view.innerHTML = `<h1>Analytics</h1><p class="sub">Log how posted clips did. With 20+ results, Clipper re-weights its quality score to match what actually works for you.</p>
    <div class="grid3">
      <div class="card"><h2>Posts tracked</h2><div style="font-size:26px;font-weight:800">${a.items.length}</div></div>
      <div class="card"><h2>Score vs real views</h2><div style="font-size:26px;font-weight:800">${a.score_vs_views_rank_correlation == null ? "—" : a.score_vs_views_rank_correlation}</div><div class="hint">Rank correlation (−1…1). Needs 5+ posts with views.</div></div>
      <div class="card"><h2>Score calibrated on</h2><div style="font-size:26px;font-weight:800">${a.calibrated_on || 0}</div><div class="row" style="margin-top:6px"><button class="btn sm" id="cal">Calibrate</button>${yt.connected ? `<button class="btn sm" id="sync">Sync YouTube</button>` : ""}</div></div>
    </div>
    ${Object.keys(a.groups).length ? `<div class="card" style="margin-top:16px"><h2>By campaign / niche</h2><table class="t"><tr><th>Group</th><th>Posts</th><th>Avg views</th><th>Likes / views</th></tr>${Object.entries(a.groups).map(([g, v]) => `<tr><td>${esc(g)}</td><td>${v.posts}</td><td>${v.avg_views.toLocaleString()}</td><td>${v.engagement}%</td></tr>`).join("")}</table></div>` : ""}
    <div class="card" style="margin-top:16px"><h2>Posted clips</h2>
      <table class="t"><tr><th>Clip</th><th>Platform</th><th>Campaign</th><th>Score</th><th>Followers</th><th>Views</th><th>Likes</th><th>Comments</th><th></th></tr>
      ${a.items.map(i => `<tr data-id="${i.id}"><td>${i.job ? `<a href="#/edit/${i.job}/${i.clip}">${esc(i.clip)}</a>` : esc(i.clip)}${i.url ? ` <a href="${esc(i.url)}" target="_blank" rel="noopener">↗</a>` : ""}</td><td>${esc(i.platform)}</td>
        <td><input type="text" data-k="campaign" value="${esc(i.campaign || "")}"></td><td>${i.score ?? "—"}</td>
        ${["followers", "views", "likes", "comments"].map(k => `<td><input type="number" data-k="${k}" value="${i[k] ?? ""}" style="width:96px"></td>`).join("")}
        <td><button class="btn sm danger" data-del="${i.id}">✕</button></td></tr>`).join("")}</table>
      <form class="row" id="add" style="margin-top:12px"><input type="text" name="job" placeholder="job id" style="width:140px"><input type="text" name="clip" placeholder="clip_01" style="width:100px">
        <select name="platform" style="width:130px">${["tiktok", "instagram", "youtube", "x", "facebook", "other"].map(p => opt(p)).join("")}</select>
        <input type="url" name="url" placeholder="post link" class="grow"><input type="number" name="views" placeholder="views" style="width:100px"><button class="btn">Add</button></form>
      <div class="hint">Tip: “job id” is the code in a project's address (#/job/<b>…</b>). Linking a clip lets calibration use its score breakdown.</div></div>
    <div class="card hint" style="margin-top:16px">How calibration works: outcome = log(1 + views ÷ followers); a least-squares fit on the 7 score parts, negative weights dropped, weights rescaled to 100. It reports how well the old vs new weights rank your clips, so you can see whether it helped.</div>`;
  $$("tr[data-id] input").forEach(inp => inp.onchange = () => api(`/api/analytics/${inp.closest("tr").dataset.id}`, { method: "PUT", body: { [inp.dataset.k]: inp.value } }).then(() => toast("Saved")));
  $$("[data-del]").forEach(b => b.onclick = async () => { await api(`/api/analytics/${b.dataset.del}`, { method: "DELETE" }); route(); });
  $("#add").onsubmit = async e => { e.preventDefault(); const f = e.target; await api("/api/analytics", { method: "POST", body: { job: f.job.value.trim(), clip: f.clip.value.trim(), platform: f.platform.value, url: f.url.value.trim(), views: f.views.value } }); route(); };
  $("#cal").onclick = async () => {
    const dry = await api("/api/analytics/calibrate", { method: "POST", body: { dry_run: true } });
    if (!dry.ok) return toast(dry.message, 4000);
    if (confirm(`Fitted on ${dry.n} clips.\nRanking quality before: ${dry.rank_correlation_before}, after: ${dry.rank_correlation_after}.\nNew weights: ${Object.entries(dry.weights).map(([k, v]) => `${k} ${v}`).join(", ")}\n\nApply?`)) {
      await api("/api/analytics/calibrate", { method: "POST", body: {} }); toast("Score calibrated"); route();
    }
  };
  $("#sync")?.addEventListener("click", async () => { try { const r = await api("/api/analytics/sync-youtube", { method: "POST" }); toast(`Updated ${r.updated} YouTube posts`); route(); } catch (e) { toast(e.message); } });
}

/* ---------------- caption remover ---------------- */
async function viewTools() {
  view.innerHTML = `<h1>Caption remover</h1><p class="sub">Best effort, not true AI inpainting: ffmpeg blends in pixels from around the caption band. Works well on a static caption bar; leaves a soft smudge over busy, moving backgrounds. Good for turning an already-captioned clip into clean B-roll, or for recaptioning it in your own style.</p>
    <div class="card stack" id="crcard">
      <div><label class="f">Video</label><input type="file" id="crfile" accept="video/*"></div>
      <div class="hint" id="crstatus"></div>
      <div id="crpreview" style="display:none">
        <div style="position:relative;display:inline-block;max-width:100%">
          <video id="crvid" muted style="max-width:100%;display:block" controls></video>
          <div id="crbox" style="position:absolute;border:2px solid #7dd3fc;background:rgba(125,211,252,.18);cursor:move"></div>
        </div>
        <div class="hint">Drag the box over the caption bar if the auto guess is off, then resize with the handle in its corner.</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="crgo">Remove captions</button><span class="hint" id="crrun"></span></div>
      </div>
      <div id="crresult"></div>
    </div>`;
  const status = $("#crstatus"), preview = $("#crpreview"), vid = $("#crvid"), box = $("#crbox"), result = $("#crresult");
  let uploaded = null, scale = 1, band = null;

  function drawBox() {
    box.style.left = `${band.x * scale}px`; box.style.top = `${band.y * scale}px`;
    box.style.width = `${band.w * scale}px`; box.style.height = `${band.h * scale}px`;
  }
  function makeDraggable() {
    let mode = null, sx = 0, sy = 0, start = null;
    box.onmousedown = e => {
      const r = box.getBoundingClientRect();
      mode = (e.clientX > r.right - 16 && e.clientY > r.bottom - 16) ? "resize" : "move";
      sx = e.clientX; sy = e.clientY; start = { ...band }; e.preventDefault();
    };
    window.addEventListener("mousemove", e => {
      if (!mode) return;
      const dx = (e.clientX - sx) / scale, dy = (e.clientY - sy) / scale;
      if (mode === "move") { band.x = Math.max(0, start.x + dx); band.y = Math.max(0, start.y + dy); }
      else { band.w = Math.max(20, start.w + dx); band.h = Math.max(20, start.h + dy); }
      drawBox();
    });
    window.addEventListener("mouseup", () => mode = null);
  }
  makeDraggable();

  $("#crfile").onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    status.textContent = "Uploading…"; preview.style.display = "none"; result.innerHTML = "";
    try {
      uploaded = await uploadFile("video", f);
      status.textContent = "Guessing the caption band…";
      band = await api("/api/tools/caption-remove/band", { method: "POST", body: { path: uploaded.path } });
      vid.src = URL.createObjectURL(f);
      vid.onloadedmetadata = () => {
        scale = vid.clientWidth / vid.videoWidth || 1;
        drawBox(); preview.style.display = "block"; status.textContent = "";
      };
    } catch (err) { status.textContent = err.message; }
  };
  window.addEventListener("resize", () => { if (vid.videoWidth) { scale = vid.clientWidth / vid.videoWidth; drawBox(); } });

  $("#crgo").onclick = async () => {
    $("#crgo").disabled = true; $("#crrun").textContent = "Removing (this re-encodes the video, can take a minute)…";
    try {
      const r = await api("/api/tools/caption-remove", { method: "POST", body: { path: uploaded.path, band } });
      result.innerHTML = `<div class="hint ok" style="margin-top:10px">Done: <code>${esc(r.path)}</code></div>`;
      $("#crrun").textContent = "";
    } catch (err) { $("#crrun").textContent = err.message; }
    $("#crgo").disabled = false;
  };
}

/* ---------------- settings ---------------- */
async function viewSettings() {
  const m = await loadMeta();
  const s = m.settings;
  view.innerHTML = `<h1>Settings</h1><p class="sub">Stored in ~/clipper/settings.json on this Mac (keys are never shown back).</p>
  <div class="grid2">
    <form class="card stack" id="sf">
      <h2>AI model</h2>
      <div><label class="f">Cloud gateway URL (OmniRoute or any OpenAI-compatible)</label><input type="text" name="cloud_url" value="${esc(s.cloud_url || "")}" placeholder="http://localhost:20128/v1"></div>
      <div><label class="f">Gateway API key ${s.cloud_key ? "<span class='pill ok'>set</span>" : ""}</label><input type="password" name="cloud_key" autocomplete="off" placeholder="${s.cloud_key ? "•••••••• (leave blank to keep)" : "paste your key"}"></div>
      ${s.cloud_url ? `<div class="row"><button type="button" class="btn sm" id="cscan">${m.cloud_scan.running ? "Checking…" : "Check which models work"}</button>
        <span class="hint grow" id="cscanres">${m.cloud_scan.running ? `Testing ${m.cloud_scan.done}/${m.cloud_scan.total}…`
          : m.cloud_scan.error ? `✗ ${esc(m.cloud_scan.error)}`
          : m.cloud_scan.checked_at ? `${m.cloud_models.length} of ${m.cloud_scan.tested} models work · checked ${new Date(m.cloud_scan.checked_at * 1000).toLocaleString()}`
          : "Sends every model one small test; only the ones that answer correctly are offered below."}</span></div>` : ""}
      <div><label class="f">Model for picking clips, reading briefs and writing posts</label><select name="model">${opt("", "Best installed local (Qwen3 8B)", s.model)}
        ${m.cloud_models.length ? `<optgroup label="Cloud · working, fastest first">${m.cloud_models.map(x => opt("cloud:" + x, "☁ " + x, s.model)).join("")}</optgroup>` : ""}
        <optgroup label="Local (Ollama)">${m.models.map(x => opt(x, x, s.model)).join("")}</optgroup></select>
        <div class="hint">Video, transcription and rendering always stay on this Mac; only text (brief + transcript) goes to the cloud model. If it fails, the job falls back to the local model.</div></div>
      ${m.cloud_models.length ? `<div class="row"><button type="button" class="btn sm" id="ctest">Test selected cloud model</button><span class="hint grow" id="ctres"></span></div>` : ""}
      <h2>Creator</h2>
      <div class="grid2"><div><label class="f">Your name (Social post frame)</label><input type="text" name="creator_name" value="${esc(s.creator_name || "")}"></div>
        <div><label class="f">Your @handle</label><input type="text" name="creator_handle" value="${esc(s.creator_handle || "")}" placeholder="@yourpage"></div></div>
      <h2>B-roll & music</h2>
      <div><label class="f">Pexels API key ${s.pexels_key ? "<span class='pill ok'>set</span>" : ""}</label><input type="password" name="pexels_key" placeholder="${s.pexels_key ? "•••••••• (leave blank to keep)" : "free at pexels.com/api"}"></div>
      <div><label class="f">Your B-roll folder</label><input type="text" name="broll_library" value="${esc(s.broll_library)}"></div>
      <div><label class="f">Your music folder</label><input type="text" name="music_library" value="${esc(s.music_library)}"></div>
      <div><label class="f">Default brand kit</label><select name="default_brand">${opt("", "None", s.default_brand)}${m.brands.map(b => opt(b.name, b.name, s.default_brand)).join("")}</select></div>
      <button class="btn primary">Save</button>
    </form>
    <div class="stack">
      <div class="card stack"><h2>YouTube</h2>
        <div class="chips"><span class="pill ${m.youtube.client_configured ? "ok" : ""}">OAuth client ${m.youtube.client_configured ? "added" : "missing"}</span><span class="pill ${m.youtube.connected ? "ok" : ""}">${m.youtube.connected ? "Connected" : "Not connected"}</span></div>
        <ol class="hint" style="margin:0;padding-left:18px"><li>console.cloud.google.com → new project → enable “YouTube Data API v3”.</li><li>OAuth consent screen (External, add yourself as test user).</li><li>Credentials → Create OAuth client ID → Desktop app → download JSON.</li><li>Upload it here, then Connect.</li></ol>
        <label class="drop"><strong>Upload OAuth client JSON</strong><input type="file" accept=".json" id="ytj"></label>
        <button class="btn" id="ytc" ${m.youtube.client_configured ? "" : "disabled"}>Connect YouTube</button>
      </div>
      <div class="card stack"><h2>Library</h2>
        ${[["font", "Fonts (.ttf/.otf)"], ["logo", "Logos (.png)"], ["music", "Music"], ["broll", "B-roll clips & images"], ["intro", "Intros / outros"], ["sfx", "Sound effects"]].map(([k, l]) => `
          <div class="row"><span class="grow">${l} <span class="hint">· ${m.assets[k].length}</span></span><label class="btn sm">Upload<input type="file" data-up="${k}" hidden multiple></label></div>
          <div class="hint" style="margin-top:-8px">${m.assets[k].slice(0, 6).map(a => esc(a.name)).join(", ")}${m.assets[k].length > 6 ? "…" : ""}</div>`).join("")}
      </div>
    </div>
  </div>`;
  $("#sf").onsubmit = async e => {
    e.preventDefault(); const f = e.target;
    const body = { model: f.model.value, cloud_url: f.cloud_url.value.trim(), creator_name: f.creator_name.value.trim(), creator_handle: f.creator_handle.value.trim(), broll_library: f.broll_library.value, music_library: f.music_library.value, default_brand: f.default_brand.value };
    if (f.pexels_key.value.trim()) body.pexels_key = f.pexels_key.value.trim();
    if (f.cloud_key.value.trim()) body.cloud_key = f.cloud_key.value.trim();
    await api("/api/settings", { method: "PUT", body }); toast("Settings saved"); route();
  };
  $("#cscan")?.addEventListener("click", async () => {
    await api("/api/settings/cloud/scan", { method: "POST" });
    const poll = async () => {
      const st = await api("/api/settings/cloud/scan");
      if (location.hash !== "#/settings") return;
      if (st.running) { $("#cscanres").textContent = `Testing ${st.done}/${st.total || "?"}…`; setTimeout(poll, 1500); }
      else { toast(st.error ? st.error : "Model check finished"); route(); }
    };
    poll();
  });
  $("#ctest")?.addEventListener("click", async () => {
    const mdl = $("#sf").model.value;
    if (!mdl.startsWith("cloud:")) { $("#ctres").textContent = "Pick a ☁ cloud model first."; return; }
    $("#ctres").textContent = "Testing…";
    try { const r = await api("/api/settings/cloud/test", { method: "POST", body: { model: mdl } });
      $("#ctres").textContent = `${r.correct ? "✓ Works" : "⚠ Answered, but wrong"} · structured JSON · ${r.seconds}s`; }
    catch (e) { $("#ctres").textContent = "✗ " + e.message; }
  });
  $("#ytj").onchange = async e => { await uploadFile("youtube_secret", e.target.files[0]); toast("OAuth client added"); route(); };
  $("#ytc").onclick = async () => { toast("Finish signing in in the browser window that opens…", 6000); try { await api("/api/settings/youtube/connect", { method: "POST" }); toast("YouTube connected"); route(); } catch (e) { toast(e.message); } };
  $$("[data-up]").forEach(inp => inp.onchange = async () => { for (const file of inp.files) { try { await uploadFile(inp.dataset.up, file); } catch (e) { toast(e.message); } } toast("Uploaded"); route(); });
}

/* reminders for due posts while the app is open */
async function checkDue() {
  try {
    const c = await api("/api/calendar");
    if (c.due.length) $("#foot").innerHTML = `<a href="#/calendar" style="color:var(--warn)">⏰ ${c.due.length} post(s) due</a>`;
    if (c.due.length && "Notification" in window && Notification.permission === "granted")
      c.due.forEach(p => { const k = `notified_${p.id}`; try { if (!localStorage.getItem(k)) { new Notification("Clipper: time to post", { body: `${p.platform} · ${p.clip}` }); localStorage.setItem(k, "1"); } } catch {} });
  } catch {}
}
if ("Notification" in window && Notification.permission === "default") document.addEventListener("click", () => Notification.requestPermission(), { once: true });
setInterval(checkDue, 60000); checkDue();
window.addEventListener("DOMContentLoaded", route);   // after editor.js has loaded too


/* ---------- reel frame picker (shared by the new-clips form and the editor) ---------- */
function frameSvg(key) {
  // generic calibration-card mockup, not a frame of any real clip — a layout sample has to read the same
  // whatever footage the user loads, so it is built the way the rest of this surface is: CSS/SVG, not a raster.
  const base = `<rect width="100" height="178" fill="#6b6f6c"/><circle cx="50" cy="75" r="17" fill="var(--line-2)"/>
    <path d="M21 178 Q21 116 50 116 Q79 116 79 178 Z" fill="var(--line-2)"/>`;
  const chrome = {
    none: "",
    headline: `<rect width="100" height="30" fill="var(--panel)"/><rect x="8" y="10" width="56" height="5" rx="1" fill="var(--muted)"/><rect x="8" y="18" width="38" height="5" rx="1" fill="var(--muted)"/>`,
    bars: `<rect width="100" height="9" fill="#0b0c0c"/><rect y="169" width="100" height="9" fill="#0b0c0c"/>`,
    cinema: `<rect width="100" height="22" fill="#0b0c0c"/><rect y="156" width="100" height="22" fill="#0b0c0c"/><rect x="0" y="87" width="100" height="1" fill="var(--accent)" opacity=".5"/>`,
    card: `<rect x="9" y="13" width="82" height="152" rx="3" fill="none" stroke="var(--accent)" stroke-width="1.5"/><path d="M9 21h8M9 13v8M91 21h-8M91 13v8" stroke="var(--accent)" stroke-width="1.5"/>`,
    square: `<rect width="100" height="42" fill="#0b0c0c"/><rect y="136" width="100" height="42" fill="#0b0c0c"/><rect x="14" y="42" width="72" height="94" fill="none" stroke="var(--line-2)" stroke-width="1"/>`,
    neon: `<rect x="2" y="2" width="96" height="174" rx="2" fill="none" stroke="var(--accent)" stroke-width="2"/><rect x="2" y="2" width="96" height="174" rx="2" fill="none" stroke="var(--accent)" stroke-width="5" opacity=".25"/>`,
    post: `<circle cx="14" cy="14" r="6" fill="var(--muted)"/><rect x="24" y="10" width="34" height="4" rx="1" fill="var(--muted)"/><rect x="24" y="17" width="20" height="4" rx="1" fill="var(--faint)"/><rect y="148" width="100" height="30" fill="var(--panel)"/><rect x="8" y="158" width="60" height="5" rx="1" fill="var(--muted)"/>`,
    polaroid: `<rect x="6" y="6" width="88" height="140" fill="none" stroke="#d8d6ce" stroke-width="5"/><rect x="6" y="146" width="88" height="26" fill="#d8d6ce"/>`,
  }[key] || "";
  return `<svg viewBox="0 0 100 178" xmlns="http://www.w3.org/2000/svg">${base}${chrome}</svg>`;
}
function framePicker(frames, current, inputAttr) {
  return `<div class="frame-grid"><input type="hidden" ${inputAttr} value="${current}">${Object.entries(frames).map(([k, l]) =>
    `<button type="button" class="frm ${k === current ? "on" : ""}" data-frame="${k}">${frameSvg(k)}<span>${l}</span></button>`).join("")}</div>`;
}
document.addEventListener("click", e => {
  const b = e.target.closest(".frm");
  if (!b) return;
  const grid = b.closest(".frame-grid"), inp = grid.querySelector("input");
  grid.querySelectorAll(".frm").forEach(x => x.classList.toggle("on", x === b));
  inp.value = b.dataset.frame;
  inp.dispatchEvent(new Event("change", { bubbles: true }));
});

document.addEventListener("click", e => {
  const b = e.target.closest(".mode");
  if (!b) return;
  const grid = b.closest(".mode-grid");
  grid.querySelectorAll(".mode").forEach(x => x.classList.toggle("on", x === b));
  grid.querySelector("input").value = b.dataset.mode;
});
