const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function capCard(c) {
  return `<div class="card"><div class="card-title">${esc(c.id)} <span class="muted">${esc(c.kind)}</span></div>
    <div class="card-desc">${esc(c.summary)}</div></div>`;
}

async function loadCatalog() {
  try {
    const items = (await window.spine.apiJSON("/v1/capabilities/catalog")).items;
    $("catalog").innerHTML = items.map(capCard).join("") || "<p class='muted'>Empty.</p>";
  } catch (e) {
    if (String(e.message) !== "session expired") $("catalog").innerHTML = "<p class='muted'>Could not load catalog.</p>";
  }
}

async function planForRepo() {
  const repo = $("repo").value.trim() || ".";
  const intent = $("intent").value.trim();
  $("status").textContent = "Planning…";
  try {
    const r = await window.spine.apiJSON("/v1/capabilities/plan", { method: "POST", body: JSON.stringify({ repo, intent: intent || null }) });
    const lines = (r.summary_lines || []).map((l) => `<li>${esc(l)}</li>`).join("");
    const p = r.plan || {};
    const skills = (p.skills && p.skills.length) ? `<div class="chips">${p.skills.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : "";
    const mcp = (p.mcp_servers && p.mcp_servers.length) ? `<p class="muted">MCP servers: ${p.mcp_servers.map(esc).join(", ")}</p>` : "";
    $("plan").innerHTML = `<div class="panel"><div class="panel-head">Plan for <code>${esc(repo)}</code>${intent ? ` · “${esc(intent)}”` : ""}</div>
      <ul class="plan-list">${lines}</ul>${skills}${mcp}</div>`;
    $("status").innerHTML = `<span class="ok">Planned.</span>`;
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

$("planbtn").onclick = planForRepo;
loadCatalog();
