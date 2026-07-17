const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function memCard(m) {
  const conf = Math.round((m.confidence || 0) * 100);
  const runs = (m.evidence && (m.evidence.run_ids || m.evidence.runs)) || [];
  const runChips = Array.isArray(runs) ? runs.map((r) => `<span class="chip">${esc(r)}</span>`).join("") : "";
  return `<div class="card">
    <div class="card-title"><span class="chip">${esc(m.kind)}</span>
      ${m.scope === "global" ? '<span class="pill skip">global</span>' : `<span class="muted">${esc(m.repo_key)}</span>`}
      <span class="muted">· ${esc(m.hits)} hit(s)</span></div>
    <div class="card-desc">${esc(m.statement)}</div>
    <div class="bars"><div class="bar-row"><span class="bar-l">confidence</span><span class="bar"><span style="width:${conf}%"></span></span><span class="bar-v">${conf}%</span></div></div>
    ${runChips ? `<div class="chips">${runChips}</div>` : ""}
  </div>`;
}

async function loadRepos() {
  try {
    const repos = (await window.spine.apiJSON("/v1/memory/repos")).repos || [];
    const sel = $("repo");
    for (const r of repos) {
      const o = document.createElement("option");
      o.value = r; o.textContent = r;
      sel.appendChild(o);
    }
  } catch (e) { /* a 401 already redirected */ }
}

async function load() {
  const p = new URLSearchParams();
  for (const k of ["repo", "kind", "query"]) {
    const v = $(k === "repo" ? "repo" : k).value.trim();
    if (v) p.set(k === "repo" ? "repo_key" : k, v);
  }
  $("status").textContent = "Loading…";
  try {
    const items = (await window.spine.apiJSON("/v1/memory?" + p.toString())).items || [];
    $("mem").innerHTML = items.length ? items.map(memCard).join("")
      : "<div class='empty'>No memories yet. They accrue as runs complete and consolidate.</div>";
    $("status").innerHTML = `<span class="ok">${items.length} memor${items.length === 1 ? "y" : "ies"}.</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

$("load").onclick = load;
loadRepos();
load();
