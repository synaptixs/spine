const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SSE_EVENTS = ["run.created", "run.stage", "run.gate", "run.completed", "approval.updated"];
const runs = new Map();  // sdlc_id → summary

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
  if (!res.ok) {
    let detail = "HTTP " + res.status;
    try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function delegate() {
  const source = $("src").value.trim();
  if (!source) return;
  $("cmsg").textContent = "Delegating…";
  try {
    const r = await api("/v1/runs/start", { method: "POST", body: JSON.stringify({ source, create_jira: $("jira").checked }) });
    $("cmsg").innerHTML = "Started <code>" + esc(r.sdlc_id) + "</code> — watch it below.";
    $("src").value = "";
    await loadFeed();
  } catch (e) { $("cmsg").innerHTML = '<span class="err">' + esc(e.message) + "</span>"; }
}

function stateClass(s) { return "pill state-" + (s || "running"); }

// Plain-language status for the feed — a PM shouldn't need to decode "running".
function lifecycle(s) {
  return ({ running: "In progress", completed: "Delivered", failed: "Stopped", pending: "Queued" })[s] || (s || "In progress");
}

// Turn an audit action (sdlc_intents_extracted) into a readable stage.
function stage(action) {
  return String(action || "working").replace(/^sdlc_/, "").replace(/_/g, " ");
}

function renderFeed() {
  const box = $("feed");
  const items = [...runs.values()].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  if (!items.length) {
    box.innerHTML = "<div class='empty'>No runs yet. Paste a source above and click <strong>Delegate</strong> "
      + "to start one — or run <code>orchestrator sdlc run --source &lt;id&gt;</code> in a terminal. "
      + "Runs only appear and progress when the worker + database are up (see the status above).</div>";
    return;
  }
  box.innerHTML = items.map((r) =>
    `<div class="run" id="run-${esc(r.sdlc_id)}">
       <div class="run-head"><code>${esc(r.sdlc_id)}</code>
         <span class="${stateClass(r.state)}">${esc(lifecycle(r.state))}</span></div>
       <div class="muted">${esc(stage(r.last_action))} · ${esc((r.updated_at || "").slice(0, 19))}
         · <a href="/trace/${esc(r.sdlc_id)}">trace →</a></div>
     </div>`).join("");
}

async function loadFeed() {
  try {
    const data = await api("/v1/runs");
    runs.clear();
    for (const r of data.items) runs.set(r.sdlc_id, r);
  } catch (e) { runs.clear(); }  // backend likely down → show the guidance empty state
  renderFeed();
}

async function loadGates() {
  const box = $("gates");
  let items = [];
  try { items = (await api("/v1/approvals")).items; } catch (e) { box.innerHTML = ""; return; }
  if (!items.length) {
    box.innerHTML = "<div class='empty'>Nothing waiting for you. A gate appears here when a run pauses "
      + "for your approval (the intent gate, then the merge gate).</div>";
    return;
  }
  box.innerHTML = items.map((a) =>
    `<div class="gate" id="gate-${esc(a.id)}">
       <div class="gate-head"><strong>Needs you</strong> · ${esc(a.title)}
         <span class="risk-${esc((a.risk_classification || "").toLowerCase())}">${esc(a.risk_classification)}</span></div>
       <div class="actions">
         <button class="primary" onclick="decide('${esc(a.id)}','approve')">Approve</button>
         <button class="danger" onclick="decide('${esc(a.id)}','reject')">Reject</button>
       </div>
     </div>`).join("");
}

async function decide(id, action) {
  try {
    await api("/v1/approvals/" + encodeURIComponent(id) + "/" + action, { method: "POST", body: "{}" });
    await loadGates();
  } catch (e) { /* a 401 already redirected */ }
}
window.decide = decide;

function applyEvent(ev) {
  if (!ev || !ev.run_id) return;
  if (ev.type === "approval.updated" || ev.type === "run.gate") { loadGates(); }
  const cur = runs.get(ev.run_id) || { sdlc_id: ev.run_id, state: "running", last_action: "", updated_at: "" };
  cur.last_action = (ev.payload && ev.payload.action) || cur.last_action;
  cur.updated_at = ev.ts || cur.updated_at;
  if (ev.payload && ev.payload.state) cur.state = ev.payload.state;
  runs.set(ev.run_id, cur);
  renderFeed();
  const el = $("run-" + ev.run_id);
  if (el) { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 800); }
}

// Live: the session cookie authenticates the same-origin EventSource automatically.
const es = new EventSource("/v1/stream");
for (const t of SSE_EVENTS) es.addEventListener(t, (e) => { try { applyEvent(JSON.parse(e.data)); } catch (_) {} });

// Show the exact CLI this composer runs — updates as you type.
function updateHint() {
  const s = $("src").value.trim();
  const mode = $("jira").checked ? " --create-jira" : " --safe";
  $("cli-hint").textContent = "orchestrator sdlc run --source " + (s || "<your source>") + mode;
}

// Tell the user whether the backend (DB) is reachable — so an empty page is explained.
async function checkBackend() {
  const bar = $("status"); const txt = $("status-text");
  try {
    const r = await fetch("/readyz");
    if (!r.ok) throw new Error();
    bar.className = "statusbar ok"; txt.textContent = "Backend ready";
  } catch (_) {
    bar.className = "statusbar down";
    txt.innerHTML = "Backend not ready — start the whole stack in one command: "
      + "<code>orchestrator up</code>. Delegating still queues a run.";
  }
}

$("delegate").onclick = delegate;
$("src").addEventListener("keydown", (e) => { if (e.key === "Enter") delegate(); });
$("src").addEventListener("input", updateHint);
$("jira").addEventListener("change", updateHint);
// One-click examples fill the source box (and refresh the CLI hint).
document.querySelectorAll(".ex[data-src]").forEach((b) => {
  b.addEventListener("click", () => { $("src").value = b.dataset.src; updateHint(); $("src").focus(); });
});

updateHint();
checkBackend();
loadGates();
loadFeed();
