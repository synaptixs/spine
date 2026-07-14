const $ = (id) => document.getElementById(id);
const AUTO = "orchestrator_console_auto";
const REFRESH_MS = 10000;
let timer = null;

function setMsg(text, cls) { const m = $("msg"); m.textContent = text || ""; m.className = cls || ""; }
function riskClass(r){ return "risk-" + (r||"").toLowerCase(); }
function stateClass(s){ return "state-" + (s||"running"); }
function esc(s){ const d=document.createElement("div"); d.textContent = s==null?"":String(s); return d.innerHTML; }

// Auth is the session cookie (same-origin fetch sends it automatically). A 401
// means the session lapsed → bounce to the login page.
async function api(path, opts){
  const res = await fetch(path, Object.assign({headers: {"Content-Type":"application/json"}}, opts||{}));
  if(res.status === 401){ window.location = "/login"; throw new Error("session expired"); }
  if(!res.ok){ throw new Error("HTTP " + res.status + " on " + path); }
  return res.status === 204 ? null : res.json();
}

async function loadAll(){
  setMsg("Loading…");
  try {
    await Promise.all([loadApprovals(), loadRuns()]);
    setMsg("Loaded " + new Date().toLocaleTimeString(), "ok");
  } catch(e){ setMsg(e.message, "err"); }
}

async function loadApprovals(){
  const data = await api("/v1/approvals");
  const box = $("approvals");
  if(!data.items.length){ box.innerHTML = "<div class='empty'>Nothing waiting for you. A gate appears here when a run pauses for approval (intent gate, then merge gate).</div>"; return; }
  box.innerHTML = "<table><thead><tr><th>id</th><th>title</th><th>risk</th><th>created</th></tr></thead><tbody>"
    + data.items.map(a =>
        `<tr class='clickable' data-id='${esc(a.id)}'><td><code>${esc(a.id)}</code></td>`
        + `<td>${esc(a.title)}</td><td class='${riskClass(a.risk_classification)}'>${esc(a.risk_classification)}</td>`
        + `<td class='muted'>${esc((a.created_at||"").slice(0,19))}</td></tr>`
        + `<tr id='d-${esc(a.id)}'><td colspan='4' style='padding:0'></td></tr>`).join("")
    + "</tbody></table>";
  box.querySelectorAll("tr.clickable").forEach(tr =>
    tr.onclick = () => toggleDetail(tr.dataset.id));
}

async function toggleDetail(id){
  const cell = $("d-"+id).firstElementChild;
  if(cell.dataset.open){ cell.innerHTML=""; delete cell.dataset.open; return; }
  cell.dataset.open = "1";
  cell.innerHTML = "<div class='detail muted'>Loading…</div>";
  try {
    const a = await api("/v1/approvals/" + encodeURIComponent(id));
    cell.innerHTML =
      `<div class='detail'><div><strong>${esc(a.title)}</strong> · `
      + `<span class='${riskClass(a.risk_classification)}'>${esc(a.risk_classification)}</span></div>`
      + `<pre>${esc(a.description)}</pre>`
      + `<label class='muted'>clarifications / release_notes (JSON patch, optional)</label>`
      + `<textarea id='patch-${esc(id)}' placeholder='{"clarifications": ["..."]}'></textarea>`
      + `<div class='actions'>`
      + `<button class='primary' onclick="decide('${esc(id)}','approve')">Approve</button>`
      + `<button onclick="decide('${esc(id)}','modify_input')">Approve w/ patch</button>`
      + `<button class='danger' onclick="decide('${esc(id)}','reject')">Reject</button>`
      + `</div></div>`;
  } catch(e){ cell.innerHTML = "<div class='detail err'>"+esc(e.message)+"</div>"; }
}

async function decide(id, action){
  let body = {};
  if(action === "modify_input"){
    const raw = $("patch-"+id).value.trim();
    if(!raw){ setMsg("modify_input needs a JSON patch", "err"); return; }
    try { body = {modified_input: JSON.parse(raw)}; }
    catch(e){ setMsg("patch is not valid JSON", "err"); return; }
  }
  setMsg("Submitting " + action + "…");
  try {
    await api("/v1/approvals/" + encodeURIComponent(id) + "/" + action,
              {method:"POST", body: JSON.stringify(body)});
    setMsg(action + " recorded for " + id, "ok");
    await loadApprovals();
  } catch(e){ setMsg(e.message, "err"); }
}

let allRuns = [];

async function loadRuns(){
  allRuns = (await api("/v1/runs")).items;
  renderRuns();
}

// Client-side filter by state (no refetch) — the runs list is already loaded.
function renderRuns(){
  const box = $("runs");
  const want = $("run-filter") ? $("run-filter").value : "";
  const runs = want ? allRuns.filter(r => r.state === want) : allRuns;
  if(!allRuns.length){ box.innerHTML = "<div class='empty'>No runs yet. Start one from the <a href='/app/inbox'>Inbox</a>, or run <code>orchestrator sdlc run --source &lt;id&gt;</code>. Runs need the worker + database up.</div>"; return; }
  if(!runs.length){ box.innerHTML = `<div class='empty'>No <strong>${esc(want)}</strong> runs.</div>`; return; }
  box.innerHTML = "<table><thead><tr><th>sdlc_id</th><th>state</th><th>last action</th>"
    + "<th>updated</th><th>events</th><th></th></tr></thead><tbody>"
    + runs.map(r =>
        `<tr class='clickable' data-id='${esc(r.sdlc_id)}'><td><code>${esc(r.sdlc_id)}</code></td>`
        + `<td class='${stateClass(r.state)}'>${esc(r.state)}</td>`
        + `<td class='muted'>${esc(r.last_action)}</td>`
        + `<td class='muted'>${esc((r.updated_at||"").slice(0,19))}</td>`
        + `<td>${esc(r.events)}</td>`
        + `<td class='run-actions'><a href='/trace/${esc(r.sdlc_id)}' target='_blank'>trace →</a>`
        + ` · <a href='#' class='export' data-id='${esc(r.sdlc_id)}'>export</a></td></tr>`
        + `<tr id='rd-${esc(r.sdlc_id)}'><td colspan='6' style='padding:0'></td></tr>`).join("")
    + "</tbody></table>";
  // Row click expands an inline trace; the trace link + export are separate actions.
  box.querySelectorAll("tr.clickable").forEach(tr =>
    tr.querySelector("td:first-child").onclick = () => toggleRunDetail(tr.dataset.id));
  box.querySelectorAll("a.export").forEach(a =>
    a.onclick = (e) => { e.preventDefault(); exportRun(a.dataset.id); });
}

async function toggleRunDetail(id){
  const cell = $("rd-"+id).firstElementChild;
  if(cell.dataset.open){ cell.innerHTML=""; delete cell.dataset.open; return; }
  cell.dataset.open = "1";
  cell.innerHTML = "<div class='detail muted'>Loading trace…</div>";
  try {
    const t = await api("/v1/tasks/" + encodeURIComponent(id) + "/trace");
    const rows = (t.audit||[]).map(e =>
      `<tr><td class='muted'>${esc((e.timestamp||"").slice(0,19))}</td><td>${esc(e.action)}</td><td class='muted'>${esc(e.actor)}</td></tr>`).join("");
    cell.innerHTML = `<div class='detail'><div class='muted'>${(t.audit||[]).length} event(s) · <a href='/trace/${esc(id)}' target='_blank'>full trace →</a></div>`
      + `<div id='ra-${esc(id)}'></div>`
      + `<table class='mini'><tbody>${rows || "<tr><td class='muted'>no events</td></tr>"}</tbody></table></div>`;
    loadRunArtifacts(id);
  } catch(e){
    cell.innerHTML = "<div class='detail muted'>No trace available for this run.</div>";
  }
}

// Architectural artifacts the pipeline persisted (M1 comprehension / M2 design).
async function loadRunArtifacts(id){
  const box = $("ra-"+id);
  if(!box) return;
  try {
    const items = (await api("/v1/runs/" + encodeURIComponent(id) + "/artifacts")).items || [];
    if(!items.length) return;
    const byKind = {};
    for(const a of items){ (byKind[a.kind] = byKind[a.kind] || []).push(a); }
    box.innerHTML = Object.keys(byKind).map(kind =>
      `<div class='artifacts'><strong>${esc(kind)} artifacts</strong> `
      + byKind[kind].map(a =>
          `<a href='/v1/runs/${esc(id)}/artifacts/download?key=${encodeURIComponent(a.key)}'>${esc(a.name)}</a>`
        ).join(" · ")
      + `</div>`).join("");
  } catch(e){ /* none / store not shared */ }
}

async function exportRun(id){
  setMsg("Exporting " + id + "…");
  try {
    const t = await api("/v1/tasks/" + encodeURIComponent(id) + "/trace");
    const blob = new Blob([JSON.stringify(t, null, 2)], {type: "application/json"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = id + ".trace.json";
    a.click();
    URL.revokeObjectURL(a.href);
    setMsg("Exported " + id, "ok");
  } catch(e){ setMsg("Could not export " + id + " (no trace yet)", "err"); }
}

// Live mode: poll on an interval. We SKIP a tick when a tab is hidden (no point
// fetching offscreen) or when any approval detail is expanded — re-rendering the
// table there would wipe the operator's in-progress decision (the JSON patch
// textarea). A manual Load or a recorded decision still refreshes immediately.
function anyDetailOpen(){ return !!document.querySelector("#approvals [data-open], #runs [data-open]"); }

async function tick(){
  if(document.hidden || anyDetailOpen()) return;
  await loadAll();
}

function setAuto(on){
  localStorage.setItem(AUTO, on ? "1" : "");
  $("auto").checked = on;
  if(timer){ clearInterval(timer); timer = null; }
  if(on){ timer = setInterval(tick, REFRESH_MS); }
}

$("refresh").onclick = loadAll;
$("auto").addEventListener("change", e => setAuto(e.target.checked));
if($("run-filter")){ $("run-filter").addEventListener("change", renderRuns); }

// The page is authed via the session, so load immediately; restore live mode.
if(localStorage.getItem(AUTO)){ setAuto(true); }
loadAll();
