const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

async function api(path) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function entityCard(e) {
  const tags = (e.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
  const spec = esc(JSON.stringify(e.spec ?? {}, null, 2));
  return `<div class="card">
    <div class="card-title">${esc(e.id)} <span class="muted">v${esc(e.version)} · ${statusPill(e.status)}</span></div>
    <div class="card-desc">${esc(e.description) || "<span class='muted'>No description.</span>"}</div>
    ${tags ? `<div class="chips">${tags}</div>` : ""}
    <details class="spec"><summary>spec</summary><pre>${spec}</pre></details>
  </div>`;
}

function statusPill(s) { return `<span class="pill stat-${esc(s)}">${esc(s)}</span>`; }

async function loadInto(elId, path, label) {
  try {
    const items = (await api(path)).items || [];
    $(elId).innerHTML = items.map(entityCard).join("") || `<p class="muted">No ${label} yet.</p>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $(elId).innerHTML = `<p class="muted">Could not load ${label}.</p>`;
  }
}

loadInto("agent-templates", "/v1/agent-templates", "agent templates");
loadInto("tool-contracts", "/v1/tool-contracts", "tool contracts");
loadInto("glossary", "/v1/glossary", "glossary entries");
