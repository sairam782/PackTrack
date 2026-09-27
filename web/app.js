/* PackTrack dashboard. Polls the API and redraws; no build step. */

const $ = (id) => document.getElementById(id);
const POLL_MS = 2500;

const fmtInt = (n) => (n ?? 0).toLocaleString();

/** Durations read better as "2h 35m" than as a pile of seconds. */
function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function fmtAgo(iso) {
  const then = new Date(iso + (iso.endsWith("Z") ? "" : "Z"));
  const secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 60) return "just now";
  return `${fmtDuration(secs)} ago`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch { /* keep the status */ }
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

/* ---------------------------------------------------------------- render */

function renderKpis(k) {
  const tiles = [
    { label: "Boxes tracked", value: fmtInt(k.total_boxes), sub: `${fmtInt(k.events)} crossings`, tone: "var(--accent)" },
    { label: "On the floor", value: fmtInt(k.in_stock), sub: "checked in, not out", tone: "var(--in)" },
    { label: "Departed", value: fmtInt(k.departed), sub: "checked out", tone: "var(--out)" },
    { label: "Median dwell", value: fmtDuration(k.median_dwell_s), sub: `${fmtInt(k.completed_trips)} complete trips`, tone: "var(--warn)" },
  ];
  $("kpis").innerHTML = tiles
    .map((t) => `<div class="kpi" style="--tone:${t.tone}">
        <div class="label">${t.label}</div>
        <div class="value">${t.value}</div>
        <div class="sub">${t.sub}</div>
      </div>`)
    .join("");
}

/**
 * Grouped bars: checked in against checked out, per hour.
 * Hand-rolled SVG rather than a chart library, so the page stays one file
 * with no build step.
 */
function renderFlow(flow) {
  const host = $("flow");
  if (!flow.length) {
    host.innerHTML = `<div class="empty">No crossings yet. Read a clip to populate this.</div>`;
    $("flowHint").textContent = "";
    return;
  }

  const W = 720, H = 220, padL = 34, padR = 12, padT = 14, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const peak = Math.max(1, ...flow.map((f) => Math.max(f.in, f.out)));
  const ticks = peak <= 4 ? peak : 4;
  const slot = plotW / flow.length;
  const barW = Math.max(5, Math.min(26, slot / 2.6));

  const lines = Array.from({ length: ticks + 1 }, (_, i) => {
    const v = Math.round((peak / ticks) * i);
    const y = padT + plotH - (v / peak) * plotH;
    return `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"/>
            <text class="axis" x="${padL - 7}" y="${y + 3}" text-anchor="end">${v}</text>`;
  }).join("");

  const cols = flow.map((f, i) => {
    const cx = padL + slot * i + slot / 2;
    const hIn = (f.in / peak) * plotH;
    const hOut = (f.out / peak) * plotH;
    const label = new Date(f.bucket + "Z").toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `<g class="col">
      <title>${label} — ${f.in} in, ${f.out} out</title>
      <rect class="bar" x="${cx - barW - 2}" y="${padT + plotH - hIn}" width="${barW}" height="${hIn}" rx="3" fill="var(--in)"/>
      <rect class="bar" x="${cx + 2}" y="${padT + plotH - hOut}" width="${barW}" height="${hOut}" rx="3" fill="var(--out)"/>
      <text class="axis" x="${cx}" y="${H - 10}" text-anchor="middle">${label}</text>
    </g>`;
  }).join("");

  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Box crossings per hour, checked in against checked out">
      ${lines}${cols}
    </svg>
    <div class="legend">
      <span><i style="background:var(--in)"></i>Checked in</span>
      <span><i style="background:var(--out)"></i>Checked out</span>
    </div>`;

  const totalIn = flow.reduce((s, f) => s + f.in, 0);
  const totalOut = flow.reduce((s, f) => s + f.out, 0);
  $("flowHint").textContent = `${totalIn} in · ${totalOut} out`;
}

function renderStations(stations) {
  $("stations").innerHTML = stations
    .map((s) => `<div class="station">
        <span class="name">${s.station_name}</span>
        <span class="role ${s.role}">${s.role}</span>
        <span class="counts"><b style="color:var(--in)">${s.checked_in} in</b> ·
          <b style="color:var(--out)">${s.checked_out} out</b></span>
      </div>`)
    .join("");
}

function renderFeed(recent) {
  const host = $("feed");
  if (!recent.length) {
    host.innerHTML = `<div class="empty">Crossings will appear here as clips are read.</div>`;
    $("feedHint").textContent = "";
    return;
  }
  host.innerHTML = recent
    .map((e) => `<div class="ev ${e.direction}">
        <span class="arrow">${e.direction === "in" ? "↓" : "↑"}</span>
        <span class="id">${e.box_id}</span>
        <span class="meta">${e.station_id}<br/>${fmtAgo(e.ts)}</span>
      </div>`)
    .join("");
  $("feedHint").textContent = `last ${recent.length}`;
}

function renderSuppliers(suppliers) {
  const host = $("suppliers");
  if (!suppliers.length) {
    host.innerHTML = `<div class="empty">No suppliers yet.</div>`;
    return;
  }
  const peak = Math.max(...suppliers.map((s) => s.total));
  host.innerHTML = suppliers
    .map((s) => {
      const gone = s.total - s.in_stock;
      return `<div class="sup-row">
        <div class="head"><b>${s.supplier}</b><span>${s.in_stock} on floor / ${s.total}</span></div>
        <div class="track">
          <i style="width:${(s.in_stock / peak) * 100}%;background:var(--in)"></i>
          <i style="width:${(gone / peak) * 100}%;background:var(--out);opacity:.55"></i>
        </div>
      </div>`;
    })
    .join("");
}

function renderBoxes(boxes) {
  const host = $("boxes");
  if (!boxes.length) {
    host.innerHTML = `<div class="empty">No boxes tracked yet.</div>`;
    $("boxHint").textContent = "";
    return;
  }
  host.innerHTML = `<table>
    <thead><tr>
      <th>Box</th><th>Supplier</th><th>Part</th><th>State</th>
      <th>Station</th><th style="text-align:right">Dwell</th><th style="text-align:right">Last seen</th>
    </tr></thead>
    <tbody>${boxes.map((b) => `<tr>
      <td class="id">${b.box_id}</td>
      <td>${b.supplier_name ?? "—"}</td>
      <td>${b.part_type ?? "—"}</td>
      <td><span class="tag ${b.state}">${b.state.replace("_", " ")}</span></td>
      <td>${b.station_id}</td>
      <td class="num">${fmtDuration(b.dwell_seconds)}</td>
      <td class="num">${fmtAgo(b.last_seen)}</td>
    </tr>`).join("")}</tbody></table>`;
  $("boxHint").textContent = `${boxes.length} tracked`;
}

/* ------------------------------------------------------------------ data */

async function refresh() {
  try {
    const data = await api("/api/dashboard");
    renderKpis(data.kpis);
    renderFlow(data.flow);
    renderStations(data.stations);
    renderFeed(data.recent);
    renderSuppliers(data.by_supplier);
    renderBoxes(data.boxes);
    $("livePill").classList.add("live");
  } catch (err) {
    $("livePill").classList.remove("live");
    $("livePill").innerHTML = `<span class="dot"></span>OFFLINE`;
    console.error(err);
  }
}

async function loadMeta() {
  const [health, stations] = await Promise.all([api("/api/health"), api("/api/stations")]);

  const pill = $("backendPill");
  pill.textContent = `QR: ${health.qr_backend}`;
  pill.title = health.qr_backend_note;
  // Say so when the weaker decoder is in play, rather than letting a missed
  // label look like a missing box.
  pill.classList.toggle("warnish", health.qr_backend !== "pyzbar");

  $("clipSel").innerHTML = health.demo_clips.length
    ? health.demo_clips.map((c) => `<option value="${c}">${c}</option>`).join("")
    : `<option value="">no clips found</option>`;

  $("stationSel").innerHTML = stations
    .map((s) => `<option value="${s.station_id}">${s.station_name} (${s.role})</option>`)
    .join("");

  // Pair the obvious clip with the obvious station, so one click does the
  // right thing during a demo.
  const guess = () => {
    const clip = $("clipSel").value.toLowerCase();
    const outbound = clip.includes("out");
    const wantedRole = outbound ? "outbound" : "inbound";
    // Prefer a station whose own name matches the clip, so "inbound.mp4" does
    // not land on whichever inbound station happens to sort first.
    const byName = stations.find((s) =>
      s.station_id.toLowerCase().includes(outbound ? "out" : "in") && s.role === wantedRole);
    const match = byName ?? stations.find((s) => s.role === wantedRole);
    if (match) $("stationSel").value = match.station_id;
    $("offset").value = outbound ? 0.5 : 3;
  };
  $("clipSel").addEventListener("change", guess);
  guess();
}

/* ------------------------------------------------------------------ jobs */

const tracked = new Map();

function renderJobs() {
  const rows = [...tracked.values()].slice(0, 5);
  $("jobs").innerHTML = rows
    .map((j) => {
      const note =
        j.status === "done" && j.result
          ? `${j.result.events} crossings from ${j.result.scans} reads`
          : j.status === "error"
            ? j.error
            : j.detail;
      return `<div class="job ${j.status}">
          <span class="nm">${j.label}</span>
          <span class="st">${note}</span>
        </div>`;
    })
    .join("");

  const running = rows.find((j) => j.status === "running");
  $("bar").style.width = running ? `${running.percent}%` : "0%";
  $("runBtn").disabled = Boolean(running);
}

async function follow(jobId) {
  while (true) {
    let snapshot;
    try {
      snapshot = await api(`/api/jobs/${jobId}`);
    } catch {
      return;
    }
    tracked.set(jobId, snapshot);
    renderJobs();
    if (snapshot.status !== "running") {
      await refresh();
      toast(
        snapshot.status === "done"
          ? `${snapshot.label}: ${snapshot.result?.events ?? 0} crossings recorded`
          : `${snapshot.label} failed: ${snapshot.error}`,
      );
      return;
    }
    await new Promise((r) => setTimeout(r, 600));
  }
}

async function runDemoClip() {
  const clip = $("clipSel").value;
  if (!clip) return toast("No demo clips found. Run tools/make_demo_video.py");
  const params = new URLSearchParams({
    clip,
    station_id: $("stationSel").value,
    offset_minutes: String(Number($("offset").value || 0) * 60),
  });
  try {
    const { job_id } = await api(`/api/video/demo?${params}`, { method: "POST" });
    follow(job_id);
  } catch (err) {
    toast(err.message);
  }
}

async function uploadClip(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  form.append("station_id", $("stationSel").value);
  form.append("offset_minutes", String(Number($("offset").value || 0) * 60));
  try {
    const { job_id } = await api("/api/video/upload", { method: "POST", body: form });
    follow(job_id);
  } catch (err) {
    toast(err.message);
  }
}

/* ------------------------------------------------------------------ wire */

$("runBtn").addEventListener("click", runDemoClip);
$("uploadBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => uploadClip(e.target.files?.[0]));

const drop = $("drop");
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => uploadClip(e.dataTransfer?.files?.[0]));
drop.addEventListener("click", () => $("fileInput").click());

$("resetBtn").addEventListener("click", async () => {
  if (!confirm("Clear every box and crossing?")) return;
  await api("/api/demo/reset", { method: "POST" });
  tracked.clear();
  renderJobs();
  await refresh();
  toast("Cleared");
});

await loadMeta();
await refresh();
setInterval(refresh, POLL_MS);
