"use strict";

// ch-autochart studio front end: upload -> progress -> studio -> details -> done.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const FRET = ["#1fb34a", "#e5383b", "#f5c518", "#2f80ed", "#f2711c"];
const FRET_DARK = ["#0d6b2a", "#8c1d20", "#9c7a06", "#174f9c", "#9a430c"];
const TRACK_COLOR = {
  guitar: "#d9a066", bass: "#7fa7c9", piano: "#b79bd8", other: "#7cc3a6",
  vocals: "#e08fa3", drums: "#8e817a", mix: "#cdbfae",
};
const STEM_ROLES = {
  guitar: ["guitar", "guitars", "gtr", "lead_guitar", "rhythm_guitar"],
  bass: ["bass"], drums: ["drums", "drum", "percussion"],
  vocals: ["vocals", "vocal", "vox", "voice"],
  piano: ["piano", "keys", "keyboard", "keyboards"],
  other: ["other", "synth", "synths", "harmonica", "strings", "organ", "horns"],
};
const ROLE_NAMES = { guitar: "Guitar", bass: "Bass", drums: "Drums", vocals: "Vocals", piano: "Piano / keys", other: "Other" };
const LOOK_AHEAD = 1.8; // seconds of chart visible on the highway

const S = {
  instrument: "guitar",
  song: null,
  stems: [],
  jobId: null,
  project: null,
  plan: [],
  preview: null,
  diff: "expert",
  view: { start: 0, pps: 40 },
  follow: true,
  mute: {},
  solo: {},
  art: null,
  audio: { ctx: null, buffers: {}, gains: {}, sources: [], playing: false, startedAt: 0, offset: 0 },
  dirty: false,
};

// ---------------------------------------------------------------- helpers

function show(id) {
  for (const s of $$(".screen")) s.hidden = s.id !== id;
  window.scrollTo(0, 0);
  if (id === "studio") requestAnimationFrame(() => { resizeAll(); drawAll(); });
}

function fmt(t, tenths = false) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return tenths ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${m}:${String(Math.floor(s)).padStart(2, "0")}`;
}

function fmtSize(bytes) {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
}

function guessRole(name) {
  const base = name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[\s-]+/g, "_");
  for (const [role, names] of Object.entries(STEM_ROLES)) {
    if (names.includes(base) || names.some((n) => base.endsWith("_" + n) || base.startsWith(n + "_"))) return role;
  }
  return "";
}

function setPressed(group, attr, value) {
  for (const b of $$(`[${attr}]`, group)) b.setAttribute("aria-pressed", String(b.getAttribute(attr) === value));
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* not json */ }
  if (!res.ok) throw new Error((body && body.error) || `The server answered ${res.status}.`);
  return body;
}

// ---------------------------------------------------------------- 1. upload

function initUpload() {
  const input = $("#song-input");
  const drop = $("#drop");
  input.addEventListener("change", () => pickSong(input.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    if (e.dataTransfer.files[0]) pickSong(e.dataTransfer.files[0]);
  });
  $(".upload-options .seg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-instrument]");
    if (!b) return;
    S.instrument = b.dataset.instrument;
    setPressed(e.currentTarget, "data-instrument", S.instrument);
  });
  $("#stems-input").addEventListener("change", (e) => {
    S.stems = [...e.target.files].map((f) => ({ file: f, role: guessRole(f.name) }));
    renderStemList();
  });
  $("#upload-form").addEventListener("submit", startJob);
  $("#retry").addEventListener("click", () => show("upload"));
}

function pickSong(file) {
  if (!file) return;
  S.song = file;
  $("#drop").classList.add("has-file");
  $("#drop-title").textContent = file.name;
  $("#drop-sub").textContent = `${fmtSize(file.size)}. Click to choose a different file.`;
  $("#start").disabled = false;
  $("#upload-error").textContent = "";
}

function renderStemList() {
  const ul = $("#stem-list");
  ul.innerHTML = "";
  S.stems.forEach((s, i) => {
    const li = document.createElement("li");
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", `What is ${s.file.name}?`);
    sel.innerHTML = `<option value="">Skip this file</option>` +
      Object.entries(ROLE_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    sel.value = s.role;
    sel.addEventListener("change", () => { S.stems[i].role = sel.value; });
    const name = document.createElement("span");
    name.textContent = s.file.name;
    li.append(sel, name);
    ul.append(li);
  });
}

async function startJob(e) {
  e.preventDefault();
  if (!S.song) return;
  const fd = new FormData();
  fd.append("song", S.song);
  fd.append("instrument", S.instrument);
  for (const s of S.stems) {
    if (!s.role) continue;
    fd.append("stems", s.file);
    fd.append("stem_role", s.role);
  }
  $("#start").disabled = true;
  $("#start").textContent = "Uploading…";
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    S.jobId = job.id;
    $("#progress-title").textContent = S.song.name.replace(/\.[^.]+$/, "");
    $("#progress-error").hidden = true;
    show("progress");
    renderProgress(job);
    pollJob();
  } catch (err) {
    $("#upload-error").textContent = err.message;
  } finally {
    $("#start").disabled = false;
    $("#start").textContent = "Analyze song";
  }
}

// ---------------------------------------------------------------- 2. progress

function renderProgress(st) {
  const ol = $("#stages");
  ol.innerHTML = "";
  st.stages.forEach((name, i) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = name;
    li.append(label);
    if (st.status === "ready" || i < st.stage) li.className = "done";
    else if (i === st.stage) li.className = "active";
    if (i === 1 && i === st.stage && st.progress != null) {
      const meter = document.createElement("div");
      meter.className = "meter";
      meter.innerHTML = `<i style="width:${Math.round(st.progress * 100)}%"></i>`;
      li.append(meter);
      label.textContent = `${name}: ${Math.round(st.progress * 100)}%`;
    }
    ol.append(li);
  });
  $("#status-line").textContent = st.status === "queued" ? "Waiting for the previous song to finish…" : st.message || "";
}

async function pollJob() {
  try {
    const st = await api(`/api/jobs/${S.jobId}`);
    renderProgress(st);
    if (st.status === "error") {
      $("#progress-error-text").textContent = st.error;
      $("#progress-error").hidden = false;
      return;
    }
    if (st.status === "ready") {
      const project = await api(`/api/jobs/${S.jobId}/project`);
      openStudio(project);
      return;
    }
  } catch (err) {
    $("#status-line").textContent = `Lost touch with the app (${err.message}). Retrying…`;
  }
  setTimeout(pollJob, 700);
}

// ---------------------------------------------------------------- 3. studio

function openStudio(project) {
  stopAudio();
  S.project = project;
  S.plan = project.recommended.slice();
  S.preview = project.preview;
  S.mute = {};
  S.solo = {};
  S.audio.offset = 0;
  for (const t of project.tracks) S.mute[t.id] = project.separated && t.id === "mix";

  $("#song-title").textContent = project.meta.title;
  $("#song-artist").textContent = project.meta.artist;
  const facts = [
    project.tempo.text + (project.tempo.mode === "variable" ? ", follows the band's tempo" : ""),
    `${fmt(project.duration)} long`,
    project.separated ? `Split into ${project.tracks.length - 1} instruments` : "Not split into instruments",
    `${project.bars.length - 1 - project.first_bar} bars`,
  ];
  $("#facts").innerHTML = "";
  for (const f of facts) {
    const span = document.createElement("span");
    span.textContent = f;
    $("#facts").append(span);
  }
  const notice = $("#notice");
  notice.hidden = !project.notice;
  notice.textContent = project.notice || "";
  $("#hint").hidden = !project.separated;
  $("#reset-plan").hidden = !project.separated;
  $("#total").textContent = fmt(project.duration);

  buildTrackRows();
  show("studio");
  setZoom(+$("#zoom").value);
  renderPlanSummary();
  loadAudio();
}

function buildTrackRows() {
  const box = $("#track-rows");
  box.innerHTML = "";
  for (const t of S.project.tracks) {
    const row = document.createElement("div");
    row.className = "row track";
    row.dataset.track = t.id;
    const nNotes = t.notes.length;
    const sub = t.id === "mix" ? (S.project.separated ? "The original song" : "Charted from the full mix")
      : t.chartable ? `${nNotes} notes heard` : "Not used for guitar charts";
    row.innerHTML = `
      <div class="label">
        <div class="name"><i style="background:${TRACK_COLOR[t.id]}"></i><span></span></div>
        <div style="display:flex;gap:10px;align-items:center;justify-content:space-between">
          <span class="sub"></span>
          <span class="mute-solo">
            <button type="button" class="m" aria-pressed="${S.mute[t.id]}" title="Mute">M</button>
            <button type="button" class="s" aria-pressed="false" title="Solo">S</button>
          </span>
        </div>
      </div>
      <canvas height="64"></canvas>`;
    $(".name span", row).textContent = t.name;
    $(".sub", row).textContent = sub;
    $(".m", row).setAttribute("aria-label", `Mute ${t.name}`);
    $(".s", row).setAttribute("aria-label", `Solo ${t.name}`);
    const canvas = $("canvas", row);
    canvas.setAttribute("aria-label", `${t.name} waveform`);
    if (t.chartable && S.project.separated) canvas.classList.add("paintable");
    $(".m", row).addEventListener("click", (e) => {
      S.mute[t.id] = !S.mute[t.id];
      e.currentTarget.setAttribute("aria-pressed", String(S.mute[t.id]));
      applyGains();
    });
    $(".s", row).addEventListener("click", (e) => {
      S.solo[t.id] = !S.solo[t.id];
      e.currentTarget.setAttribute("aria-pressed", String(S.solo[t.id]));
      applyGains();
    });
    attachPainter(canvas, t);
    box.append(row);
  }
}

// ----- geometry

function timelineWidth() {
  const c = $("#ruler");
  return c.clientWidth || 600;
}

function setZoom(z) {
  const w = timelineWidth();
  const fit = w / Math.max(S.project.duration, 1);
  const max = 260;
  const center = S.view.start + w / S.view.pps / 2;
  S.view.pps = fit * Math.pow(max / fit, z / 100);
  S.view.start = clampStart(center - w / S.view.pps / 2);
  syncScrollbar();
  drawAll();
}

function clampStart(start) {
  const span = timelineWidth() / S.view.pps;
  return Math.min(Math.max(start, 0), Math.max(0, S.project.duration - span));
}

function syncScrollbar() {
  const span = timelineWidth() / S.view.pps;
  const room = Math.max(0, S.project.duration - span);
  const hs = $("#hscroll");
  hs.disabled = room <= 0;
  hs.value = room > 0 ? Math.round((S.view.start / room) * 1000) : 0;
}

const tx = (t) => (t - S.view.start) * S.view.pps;
const xt = (x) => S.view.start + x / S.view.pps;

function barAt(t) {
  const bars = S.project.bars;
  let lo = 0, hi = bars.length - 2;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (bars[mid] <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function setupCanvas(c) {
  const dpr = window.devicePixelRatio || 1;
  if (!c.dataset.h) c.dataset.h = c.getAttribute("height");
  const h = +c.dataset.h, w = c.clientWidth;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    c.style.height = `${h}px`;
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function resizeAll() {
  const hw = $("#highway");
  const panel = hw.parentElement;
  const timelineH = $("#timeline").offsetHeight;
  hw.dataset.h = Math.max(420, (window.innerWidth > 1100 ? timelineH : 460) - 90);
  void panel;
  drawAll();
}

// ----- drawing

function drawAll() {
  if (!S.project || $("#studio").hidden) return;
  drawRuler();
  drawPlan();
  for (const row of $$("#track-rows .row")) drawTrack(row);
  drawStrip();
  drawHighway();
  $("#now").textContent = fmt(now(), true);
}

function playheadAt(ctx, h) {
  const x = tx(now());
  if (x < 0 || x > ctx.canvas.clientWidth) return;
  ctx.fillStyle = "#e2be77";
  ctx.fillRect(Math.round(x) - 1, 0, 2, h);
}

function visibleBars() {
  const bars = S.project.bars;
  const t0 = S.view.start, t1 = xt(timelineWidth());
  const out = [];
  for (let b = 0; b < bars.length - 1; b++) {
    if (bars[b + 1] < t0 || bars[b] > t1) continue;
    out.push(b);
  }
  return out;
}

function drawRuler() {
  const { ctx, w, h } = setupCanvas($("#ruler"));
  ctx.clearRect(0, 0, w, h);
  const bars = S.project.bars, first = S.project.first_bar;
  const barPx = (bars[first + 1] - bars[first]) * S.view.pps;
  const every = barPx > 34 ? 1 : barPx > 16 ? 2 : barPx > 8 ? 4 : barPx > 4 ? 8 : 16;
  ctx.font = "600 11px Archivo, system-ui, sans-serif";
  for (const b of visibleBars()) {
    const x = tx(bars[b]);
    const n = b - first + 1;
    const labelled = n >= 1 && (n - 1) % every === 0;
    ctx.fillStyle = labelled ? "#6b5a54" : "#4d3b36";
    ctx.fillRect(Math.round(x), labelled ? 24 : 34, 1, labelled ? 20 : 10);
    if (labelled) {
      ctx.fillStyle = "#a8988b";
      ctx.fillText(String(n), x + 4, 38);
    }
  }
  ctx.font = "700 12px Archivo, system-ui, sans-serif";
  for (const s of S.project.sections) {
    const x = tx(s.time);
    if (x > w || x < -200) continue;
    ctx.fillStyle = "#c9a15a";
    ctx.fillRect(Math.round(x), 2, 2, 18);
    ctx.fillStyle = "#ede3d6";
    ctx.fillText(s.name, x + 6, 15);
  }
  playheadAt(ctx, h);
}

function hatch(ctx, x, y, w, h) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.strokeStyle = "rgba(168,152,139,.18)";
  ctx.lineWidth = 1;
  for (let i = -h; i < w; i += 7) {
    ctx.beginPath();
    ctx.moveTo(x + i, y + h);
    ctx.lineTo(x + i + h, y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawPlan() {
  const { ctx, w, h } = setupCanvas($("#plan"));
  ctx.clearRect(0, 0, w, h);
  const bars = S.project.bars;
  for (const b of visibleBars()) {
    const x0 = Math.max(tx(bars[b]), tx(0));
    const x1 = Math.min(tx(bars[b + 1]), tx(S.project.duration));
    if (x1 <= x0) continue;
    const who = S.plan[b];
    if (who) {
      ctx.fillStyle = TRACK_COLOR[who];
      ctx.fillRect(x0, 6, x1 - x0, h - 12);
    } else {
      hatch(ctx, x0, 6, x1 - x0, h - 12);
    }
    ctx.fillStyle = "#30221f";
    ctx.fillRect(Math.round(x1) - 1, 6, 1, h - 12);
  }
  playheadAt(ctx, h);
}

function drawTrack(row) {
  const id = row.dataset.track;
  const t = S.project.tracks.find((x) => x.id === id);
  const c = $("canvas", row);
  const { ctx, w, h } = setupCanvas(c);
  ctx.clearRect(0, 0, w, h);
  const color = TRACK_COLOR[id];
  const bars = S.project.bars;

  // highlight the bars this track is charted in
  if (S.project.separated) {
    for (const b of visibleBars()) {
      if (S.plan[b] !== id) continue;
      const x0 = Math.max(tx(bars[b]), 0), x1 = Math.min(tx(bars[b + 1]), w);
      ctx.fillStyle = color + "2e";
      ctx.fillRect(x0, 0, x1 - x0, h);
      ctx.fillStyle = color;
      ctx.fillRect(x0, 0, x1 - x0, 2);
      ctx.fillRect(x0, h - 2, x1 - x0, 2);
    }
  }

  // waveform
  const peaks = t.peaks, pps = S.project.peaks_per_sec;
  const mid = h / 2;
  ctx.fillStyle = t.chartable || id === "mix" ? color : color + "99";
  const step = 1;
  for (let x = 0; x < w; x += step) {
    const a = Math.floor(xt(x) * pps), b = Math.max(a + 1, Math.floor(xt(x + step) * pps));
    if (b <= 0 || a >= peaks.length) continue;
    let m = 0;
    for (let i = Math.max(a, 0); i < Math.min(b, peaks.length); i++) if (peaks[i] > m) m = peaks[i];
    const amp = (m / 255) * (mid - 4);
    if (amp > 0.3) ctx.fillRect(x, mid - amp, step, amp * 2);
  }

  // where notes were heard
  if (t.notes.length && t.chartable) {
    ctx.fillStyle = "rgba(237,227,214,.55)";
    const t0 = S.view.start, t1 = xt(w);
    for (const n of t.notes) {
      if (n[0] < t0 || n[0] > t1) continue;
      ctx.fillRect(Math.round(tx(n[0])), h - 7, 1, 5);
    }
  }
  row.classList.toggle("dim", isSilenced(id));
  playheadAt(ctx, h);
}

function currentChart() {
  return S.preview && S.preview.charts[S.diff];
}

function drawStrip() {
  const c = $("#strip");
  const { ctx, w, h } = setupCanvas(c);
  ctx.clearRect(0, 0, w, h);
  const chart = currentChart();
  const lanes = 5, pad = 8, laneH = (h - pad * 2) / lanes;
  for (let i = 0; i < lanes; i++) {
    ctx.fillStyle = i % 2 ? "rgba(0,0,0,.10)" : "rgba(0,0,0,.18)";
    ctx.fillRect(0, pad + i * laneH, w, laneH);
  }
  const bars = S.project.bars;
  ctx.fillStyle = "rgba(168,152,139,.18)";
  for (const b of visibleBars()) ctx.fillRect(Math.round(tx(bars[b])), pad, 1, h - pad * 2);
  if (!chart) {
    ctx.fillStyle = "#a8988b";
    ctx.font = "13px Archivo, system-ui, sans-serif";
    ctx.fillText("Nothing charted. Highlight a passage on one of the tracks above.", 12, h / 2 + 4);
    playheadAt(ctx, h);
    return;
  }
  ctx.fillStyle = "rgba(140, 220, 255, .10)";
  for (const [a, b] of chart.star_power) ctx.fillRect(tx(a), pad, (b - a) * S.view.pps, h - pad * 2);
  const t0 = S.view.start - 4, t1 = xt(w);
  const gem = Math.max(3, Math.min(9, S.view.pps * 0.06));
  for (const [t, frets, sus] of chart.notes) {
    if (t + sus < t0 || t > t1) continue;
    const x = tx(t);
    for (const f of frets) {
      const y = pad + f * laneH + laneH / 2;
      if (sus > 0) {
        ctx.fillStyle = FRET[f] + "aa";
        ctx.fillRect(x, y - 1.5, sus * S.view.pps, 3);
      }
      ctx.fillStyle = FRET[f];
      ctx.beginPath();
      ctx.roundRect(x - gem / 2, y - laneH * 0.32, gem, laneH * 0.64, 2);
      ctx.fill();
    }
  }
  playheadAt(ctx, h);
}

// ----- the highway

function drawHighway() {
  const c = $("#highway");
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = +c.dataset.h || 460;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    c.style.height = `${h}px`;
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const t = now();
  const strikeY = h - 62, horizonY = 18;
  const cx = w / 2, baseW = Math.min(w * 0.86, 250), laneW = baseW / 5;
  const persp = (z) => 1 / (1 + 1.7 * z);
  const pFar = persp(1);
  const Y = (z) => strikeY - (strikeY - horizonY) * (1 - persp(z)) / (1 - pFar);
  const X = (lane, z) => cx + (lane - 2) * laneW * persp(z);
  const zOf = (time) => (time - t) / LOOK_AHEAD;

  // neck
  const zTop = 1, zBot = -0.06;
  const neck = ctx.createLinearGradient(0, horizonY, 0, h);
  neck.addColorStop(0, "#1d1414");
  neck.addColorStop(1, "#3a2723");
  ctx.fillStyle = neck;
  ctx.beginPath();
  ctx.moveTo(cx - baseW / 2 * persp(zTop), Y(zTop));
  ctx.lineTo(cx + baseW / 2 * persp(zTop), Y(zTop));
  ctx.lineTo(cx + baseW / 2 * persp(zBot) * 1.0, h);
  ctx.lineTo(cx - baseW / 2 * persp(zBot) * 1.0, h);
  ctx.closePath();
  ctx.fill();

  // lane strings and brass rails
  for (let i = 0; i <= 5; i++) {
    const lx = i - 2.5;
    ctx.strokeStyle = i === 0 || i === 5 ? "#c9a15a" : "rgba(237,227,214,.10)";
    ctx.lineWidth = i === 0 || i === 5 ? 2 : 1;
    ctx.beginPath();
    ctx.moveTo(cx + lx * laneW * persp(zTop), Y(zTop));
    ctx.lineTo(cx + lx * laneW * persp(zBot), Y(zBot));
    ctx.stroke();
  }

  // bar lines (brass frets) and pearl inlays
  const bars = S.project.bars, first = S.project.first_bar;
  for (let b = Math.max(0, barAt(t) - 1); b < bars.length - 1; b++) {
    const z = zOf(bars[b]);
    if (z > zTop) break;
    if (z < zBot) continue;
    const half = baseW / 2 * persp(z);
    ctx.fillStyle = `rgba(201,161,90,${0.25 + 0.5 * persp(z)})`;
    ctx.fillRect(cx - half, Y(z) - 1, half * 2, Math.max(1, 2.5 * persp(z)));
    for (let k = 1; k < 4; k++) {   // beats
      const zb = zOf(bars[b] + (bars[b + 1] - bars[b]) * k / 4);
      if (zb > zTop || zb < zBot) continue;
      ctx.fillStyle = "rgba(237,227,214,.06)";
      const hb = baseW / 2 * persp(zb);
      ctx.fillRect(cx - hb, Y(zb), hb * 2, 1);
    }
    const n = b - first + 1;
    if ([3, 5, 7, 9, 0].includes(n % 12) && n > 0) {   // inlays like a real fretboard
      const zm = zOf((bars[b] + bars[b + 1]) / 2);
      if (zm <= zTop && zm >= zBot) {
        ctx.fillStyle = "rgba(243,235,221,.16)";
        const r = 7 * persp(zm);
        const spots = n % 12 === 0 ? [-0.9, 0.9] : [0];
        for (const sx of spots) {
          ctx.beginPath();
          ctx.ellipse(cx + sx * laneW * persp(zm), Y(zm), r, r * 0.55, 0, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
  }

  const chart = currentChart();
  const sp = chart ? chart.star_power : [];
  const inSP = (time) => sp.some(([a, b]) => time >= a && time < b);
  const lit = [false, false, false, false, false];

  if (chart) {
    // sustains first, so gems sit on top of them
    for (const [nt, frets, sus] of chart.notes) {
      if (!sus || nt > t + LOOK_AHEAD || nt + sus < t - 0.1) continue;
      const z0 = Math.max(zOf(nt), 0), z1 = Math.min(zOf(nt + sus), zTop);
      if (z1 <= z0) continue;
      for (const f of frets) {
        const w0 = laneW * 0.13 * persp(z0), w1 = laneW * 0.13 * persp(z1);
        ctx.fillStyle = inSP(nt) ? "rgba(170,230,255,.75)" : FRET[f] + "cc";
        ctx.beginPath();
        ctx.moveTo(X(f, z0) - w0, Y(z0));
        ctx.lineTo(X(f, z1) - w1, Y(z1));
        ctx.lineTo(X(f, z1) + w1, Y(z1));
        ctx.lineTo(X(f, z0) + w0, Y(z0));
        ctx.closePath();
        ctx.fill();
        if (nt <= t && nt + sus > t && S.audio.playing) lit[f] = true;
      }
    }
    // gems, far to near
    const visible = chart.notes.filter(([nt]) => nt >= t - 0.12 && nt <= t + LOOK_AHEAD);
    for (let i = visible.length - 1; i >= 0; i--) {
      const [nt, frets, , hopo] = visible[i];
      const z = zOf(nt);
      const s = persp(z);
      const star = inSP(nt);
      for (const f of frets) {
        const gx = X(f, z), gy = Y(z);
        const r = laneW * (hopo ? 0.30 : 0.38) * s;
        ctx.fillStyle = star ? "#bfefff" : FRET[f];
        ctx.strokeStyle = star ? "#5fb8d9" : FRET_DARK[f];
        ctx.lineWidth = Math.max(1, 3 * s);
        ctx.beginPath();
        ctx.ellipse(gx, gy, r, r * 0.6, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = hopo ? "#ffffff" : "rgba(255,255,255,.55)";
        ctx.beginPath();
        ctx.ellipse(gx, gy - r * 0.08, r * (hopo ? 0.42 : 0.32), r * (hopo ? 0.24 : 0.16), 0, 0, Math.PI * 2);
        ctx.fill();
        if (Math.abs(nt - t) < 0.06 && S.audio.playing) lit[f] = true;
      }
    }
  }

  // strike line: fret buttons
  for (let f = 0; f < 5; f++) {
    const gx = X(f, 0), r = laneW * 0.40;
    ctx.lineWidth = 3;
    ctx.strokeStyle = FRET[f];
    ctx.fillStyle = lit[f] ? FRET[f] : "rgba(0,0,0,.35)";
    ctx.beginPath();
    ctx.ellipse(gx, strikeY, r, r * 0.6, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    if (lit[f]) {
      ctx.fillStyle = "rgba(255,255,255,.6)";
      ctx.beginPath();
      ctx.ellipse(gx, strikeY, r * 0.45, r * 0.26, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // who the chart is following right now
  const who = S.plan[barAt(t)];
  const label = who ? S.project.tracks.find((x) => x.id === who)?.name : (S.project.separated ? "A rest" : "Full mix");
  $("#following").innerHTML = "";
  if (label) {
    const dot = document.createElement("i");
    dot.style.background = who ? TRACK_COLOR[who] : "transparent";
    $("#following").append(dot, document.createTextNode(`Following: ${label}`));
  }
}

// ----- plan editing

function attachPainter(canvas, track) {
  if (!track.chartable || !S.project.separated) return;
  let mode = null, anchor = 0, before = null;
  const barFromEvent = (e) => {
    const r = canvas.getBoundingClientRect();
    return barAt(Math.max(0, xt(e.clientX - r.left)));
  };
  const apply = (b) => {
    const [a, z] = anchor <= b ? [anchor, b] : [b, anchor];
    S.plan = before.slice();
    for (let i = a; i <= z; i++) {
      if (mode === "paint") S.plan[i] = track.id;
      else if (S.plan[i] === track.id) S.plan[i] = null;
    }
    drawPlan();
    for (const row of $$("#track-rows .row")) drawTrack(row);
  };
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    canvas.setPointerCapture(e.pointerId);
    anchor = barFromEvent(e);
    before = S.plan.slice();
    mode = S.plan[anchor] === track.id ? "erase" : "paint";
    apply(anchor);
  });
  canvas.addEventListener("pointermove", (e) => { if (mode) apply(barFromEvent(e)); });
  const end = () => {
    if (!mode) return;
    mode = null;
    renderPlanSummary();
    requestPreview();
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
}

let previewTimer = null, previewSeq = 0;
function requestPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const seq = ++previewSeq;
    try {
      const pv = await api(`/api/jobs/${S.jobId}/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: S.plan }),
      });
      if (seq !== previewSeq) return;
      S.preview = pv;
      renderPlanSummary();
      drawStrip();
      drawHighway();
    } catch (err) {
      $("#plan-summary").textContent = `Couldn't update the preview: ${err.message}`;
    }
  }, 200);
}

function renderPlanSummary() {
  const p = S.project;
  const counts = {};
  let total = 0;
  for (let b = p.first_bar; b < S.plan.length; b++) {
    if (p.bars[b] >= p.duration) break;
    total++;
    const k = S.plan[b] || "rest";
    counts[k] = (counts[k] || 0) + 1;
  }
  const chips = $("#plan-chips");
  chips.innerHTML = "";
  const order = ["guitar", "bass", "piano", "other", "vocals", "mix", "rest"];
  for (const k of order) {
    if (!counts[k]) continue;
    const chip = document.createElement("span");
    chip.className = "chip";
    const dot = document.createElement("i");
    dot.style.background = k === "rest" ? "transparent" : TRACK_COLOR[k];
    if (k === "rest") dot.style.border = "1px solid #a8988b";
    const name = k === "rest" ? "Rests" : (p.tracks.find((t) => t.id === k)?.name || k);
    chip.append(dot, document.createTextNode(`${name} ${Math.round((counts[k] / total) * 100)}%`));
    chips.append(chip);
  }
  const edited = S.plan.some((v, i) => v !== p.recommended[i]);
  const lines = S.preview?.summary || [];
  let text;
  if (!p.separated) text = "The song wasn't split into instruments, so the chart follows everything you hear.";
  else if (lines.length) text = `${edited ? "Your plan" : "Recommended"}: ${p.instrument} except ${lines.join("; ")}.`;
  else text = `${edited ? "Your plan" : "Recommended"}: the ${p.instrument} all the way through.`;
  $("#plan-summary").textContent = text;
  const chart = currentChart();
  $("#strip-title").textContent = `Chart preview`;
  $("#strip-sub").textContent = chart ? `${S.diff[0].toUpperCase() + S.diff.slice(1)}: ${chart.notes.length} notes` : "No notes";
  $("#hw-stats").innerHTML = "";
  if (chart) {
    const nps = chart.notes.length / Math.max(p.duration, 1);
    for (const s of [`${chart.notes.length} notes`, `${chart.chords} chords`, `${chart.star_power.length} star power phrases`, `${nps.toFixed(1)} notes a second`]) {
      const span = document.createElement("span");
      span.textContent = s;
      $("#hw-stats").append(span);
    }
  }
}

// ----- audio

function isSilenced(id) {
  const anySolo = Object.values(S.solo).some(Boolean);
  return anySolo ? !S.solo[id] : !!S.mute[id];
}

function applyGains() {
  for (const t of S.project.tracks) {
    const g = S.audio.gains[t.id];
    if (g) g.gain.setTargetAtTime(isSilenced(t.id) ? 0 : 1, S.audio.ctx.currentTime, 0.015);
  }
  for (const row of $$("#track-rows .row")) row.classList.toggle("dim", isSilenced(row.dataset.track));
}

async function loadAudio() {
  const A = S.audio;
  if (!A.ctx) {
    try { A.ctx = new AudioContext({ sampleRate: 22050 }); } catch { A.ctx = new AudioContext(); }
  }
  A.buffers = {};
  A.gains = {};
  const tracks = S.project.tracks;
  let done = 0;
  $("#loading-audio").textContent = `Loading audio 0/${tracks.length}`;
  await Promise.all(tracks.map(async (t) => {
    try {
      const data = await (await fetch(t.url)).arrayBuffer();
      A.buffers[t.id] = await A.ctx.decodeAudioData(data);
      const g = A.ctx.createGain();
      g.gain.value = isSilenced(t.id) ? 0 : 1;
      g.connect(A.ctx.destination);
      A.gains[t.id] = g;
    } catch (err) {
      console.warn("audio", t.id, err);
    }
    done++;
    $("#loading-audio").textContent = done < tracks.length ? `Loading audio ${done}/${tracks.length}` : "";
  }));
}

function now() {
  const A = S.audio;
  return A.playing ? A.offset + (A.ctx.currentTime - A.startedAt) : A.offset;
}

function play() {
  const A = S.audio;
  if (!A.ctx || !Object.keys(A.buffers).length) return;
  A.ctx.resume();
  if (A.offset >= S.project.duration - 0.05) A.offset = 0;
  A.startedAt = A.ctx.currentTime + 0.03;
  A.sources = [];
  for (const [id, buf] of Object.entries(A.buffers)) {
    const src = A.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(A.gains[id]);
    src.start(A.startedAt, Math.min(A.offset, buf.duration));
    A.sources.push(src);
  }
  A.playing = true;
  setPlayIcon();
  tick();
}

function stopAudio() {
  const A = S.audio;
  for (const s of A.sources) { try { s.stop(); } catch { /* already stopped */ } }
  A.sources = [];
  if (A.playing) A.offset = now();
  A.playing = false;
  setPlayIcon();
}

function seek(t) {
  const wasPlaying = S.audio.playing;
  stopAudio();
  S.audio.offset = Math.min(Math.max(0, t), S.project.duration);
  if (wasPlaying) play();
  drawAll();
}

function setPlayIcon() {
  const playing = S.audio.playing;
  $("#play").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("#play-icon").innerHTML = playing
    ? '<rect x="4" y="3" width="4.5" height="14" fill="currentColor"/><rect x="11.5" y="3" width="4.5" height="14" fill="currentColor"/>'
    : '<path d="M5 3l12 7-12 7z" fill="currentColor"/>';
}

function tick() {
  if (!S.audio.playing) return;
  const t = now();
  if (t >= S.project.duration) {
    stopAudio();
    S.audio.offset = S.project.duration;
  }
  if (S.follow) {
    const span = timelineWidth() / S.view.pps;
    if (t > S.view.start + span * 0.85 || t < S.view.start) {
      S.view.start = clampStart(t - span * 0.15);
      syncScrollbar();
    }
  }
  drawAll();
  requestAnimationFrame(tick);
}

function initStudio() {
  $("#play").addEventListener("click", () => (S.audio.playing ? stopAudio() : play()));
  $("#zoom").addEventListener("input", (e) => setZoom(+e.target.value));
  $("#follow").addEventListener("change", (e) => { S.follow = e.target.checked; });
  $("#hscroll").addEventListener("input", (e) => {
    const span = timelineWidth() / S.view.pps;
    S.view.start = (+e.target.value / 1000) * Math.max(0, S.project.duration - span);
    drawAll();
  });
  $("#diff-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-diff]");
    if (!b) return;
    S.diff = b.dataset.diff;
    setPressed(e.currentTarget, "data-diff", S.diff);
    renderPlanSummary();
    drawStrip();
    drawHighway();
  });
  const seekFrom = (canvas) => (e) => {
    const r = canvas.getBoundingClientRect();
    seek(xt(e.clientX - r.left));
  };
  for (const id of ["#ruler", "#plan", "#strip"]) {
    const c = $(id);
    c.addEventListener("pointerdown", seekFrom(c));
  }
  $("#timeline").addEventListener("wheel", (e) => {
    if (!S.project) return;
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const z = $("#zoom");
      z.value = Math.min(100, Math.max(0, +z.value - Math.sign(e.deltaY) * 6));
      setZoom(+z.value);
    } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey) {
      e.preventDefault();
      const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      S.view.start = clampStart(S.view.start + d / S.view.pps);
      syncScrollbar();
      drawAll();
    }
  }, { passive: false });
  $("#reset-plan").addEventListener("click", () => {
    S.plan = S.project.recommended.slice();
    renderPlanSummary();
    drawAll();
    requestPreview();
  });
  $("#approve").addEventListener("click", openDetails);
  window.addEventListener("resize", () => { if (S.project) { resizeAll(); setZoom(+$("#zoom").value); } });
  document.addEventListener("keydown", (e) => {
    if ($("#studio").hidden || e.target.closest("input, select, textarea, button")) return;
    if (e.code === "Space") { e.preventDefault(); S.audio.playing ? stopAudio() : play(); }
    else if (e.code === "ArrowRight") seek(now() + 5);
    else if (e.code === "ArrowLeft") seek(now() - 5);
    else if (e.code === "Home") seek(0);
  });
}

// ---------------------------------------------------------------- 4. details

function openDetails() {
  stopAudio();
  const f = $("#details-form");
  for (const [k, v] of Object.entries(S.project.meta)) {
    if (f.elements[k] && !f.elements[k].dataset.touched) f.elements[k].value = v || "";
  }
  $("#split-wrap").hidden = !S.project.separated;
  $("#install-wrap").hidden = !S.project.songs_folder;
  $("#install-path").textContent = S.project.songs_folder || "";
  $("#details-error").textContent = "";
  show("details");
}

function setArt(file) {
  S.art = file || null;
  const img = $("#art-img");
  if (file) {
    img.src = URL.createObjectURL(file);
    img.hidden = false;
    $("#art-hint").hidden = true;
  } else {
    img.removeAttribute("src");
    img.hidden = true;
    $("#art-hint").hidden = false;
    $("#art-input").value = "";
  }
  $("#art-clear").hidden = !file;
}

function initDetails() {
  const drop = $("#art-drop");
  $("#art-input").addEventListener("change", (e) => setArt(e.target.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith("image/")) setArt(f);
  });
  $("#art-clear").addEventListener("click", () => setArt(null));
  for (const el of $$("#details-form input[type=text]")) el.addEventListener("input", () => { el.dataset.touched = "1"; });
  $("#back-to-studio").addEventListener("click", () => show("studio"));
  $("#details-form").addEventListener("submit", buildChart);
  $("#edit-again").addEventListener("click", () => show("studio"));
  $("#another").addEventListener("click", () => {
    stopAudio();
    S.project = null;
    S.song = null;
    S.stems = [];
    $("#upload-form").reset();
    $("#stem-list").innerHTML = "";
    $("#drop").classList.remove("has-file");
    $("#drop-title").textContent = "Drop a song here, or click to choose one";
    $("#drop-sub").textContent = "MP3, OGG, WAV, FLAC or M4A";
    $("#start").disabled = true;
    for (const el of $$("#details-form input[type=text]")) delete el.dataset.touched;
    setArt(null);
    show("upload");
  });
}

async function buildChart(e) {
  e.preventDefault();
  const form = $("#details-form");
  const fd = new FormData();
  for (const k of ["title", "artist", "album", "year", "genre", "charter"]) fd.append(k, form.elements[k].value);
  for (const d of ["expert", "hard", "medium", "easy"]) fd.append(`diff_${d}`, form.elements[`diff_${d}`].checked ? "on" : "off");
  if (!["expert", "hard", "medium", "easy"].some((d) => form.elements[`diff_${d}`].checked)) {
    $("#details-error").textContent = "Pick at least one difficulty.";
    return;
  }
  fd.append("split_audio", form.elements.split_audio.checked && S.project.separated ? "on" : "off");
  fd.append("install", form.elements.install.checked ? "on" : "off");
  fd.append("plan", JSON.stringify(S.plan));
  if (S.art) fd.append("art", S.art);
  const btn = $("#build");
  btn.disabled = true;
  btn.textContent = "Building…";
  $("#details-error").textContent = "";
  try {
    const res = await api(`/api/jobs/${S.jobId}/build`, { method: "POST", body: fd });
    showDone(res, form.elements.title.value, form.elements.artist.value);
  } catch (err) {
    $("#details-error").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Build chart";
  }
}

function showDone(res, title, artist) {
  const name = res.filename.replace(/\.zip$/, "");
  $("#done-name").textContent = name;
  $("#done-files").textContent = `${res.files.join(", ")}. ${fmtSize(res.size)} zipped.`;
  const a = $("#download");
  a.href = res.download;
  a.setAttribute("download", res.filename);
  a.textContent = `Download ${res.filename}`;
  const img = $("#done-art");
  img.hidden = !S.art;
  $("#done-no-art").hidden = !!S.art;
  if (S.art) img.src = $("#art-img").src;
  const counts = ["expert", "hard", "medium", "easy"].filter((d) => d in res.counts)
    .map((d) => `${res.counts[d]} ${d}`).join(", ");
  $("#done-lede").textContent = `${title || "Your song"} by ${artist || "an unknown artist"}: ${counts} notes.` +
    (res.installed ? ` It's also in your Clone Hero songs folder.` : "");
  const steps = $("#next-steps");
  steps.innerHTML = "";
  const items = res.installed
    ? ["Open Clone Hero.", "Go to Settings, then General, and choose Scan Songs.", "Find it in Quickplay."]
    : ["Unzip the download.", "Move the folder inside it into your Clone Hero Songs folder.",
       "In Clone Hero, go to Settings, then General, and choose Scan Songs."];
  for (const s of items) {
    const li = document.createElement("li");
    li.textContent = s;
    steps.append(li);
  }
  show("done");
}

// ----------------------------------------------------------------

initUpload();
initStudio();
initDetails();
