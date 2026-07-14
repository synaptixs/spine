const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

async function api(path) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function vettingPill(v) { return `<span class="pill vet-${esc(v)}">${esc(v)}</span>`; }
function statusPill(s) { return `<span class="pill stat-${esc(s)}">${esc(s)}</span>`; }
function phaseChips(phases) {
  return (phases || []).map((p) => `<span class="phase">${esc(p)}</span>`).join(" ");
}

function outputsTable(outputs) {
  if (!outputs || !outputs.length) return "";
  const rows = outputs.map((o) =>
    `<tr><td><code>${esc(o.name)}</code></td><td class="muted">${esc(o.type)}</td><td>${esc(o.description)}</td></tr>`).join("");
  return `<div class="spec-label">Returns</div><table class="mini"><tbody>${rows}</tbody></table>`;
}

function personaCard(p) {
  const skills = p.skills.map((s) => `<span class="chip">${esc(s)}</span>`).join("");
  return `<div class="card">
    <div class="card-title">${esc(p.id)} <span class="muted">v${esc(p.version)} · ${esc(p.workflow_slot)} · ${esc(p.model)}</span></div>
    <div class="card-desc">${esc(p.description) || esc(p.role)}</div>
    <div class="chips">${skills}</div>
    <details class="spec"><summary>instructions &amp; outputs</summary>
      <div class="spec-label">Instructions</div><pre>${esc(p.role)}</pre>
      ${outputsTable(p.outputs)}
    </details>
  </div>`;
}

function skillCard(s) {
  const score = s.score != null ? ` <span class="muted">· held-out ${Math.round(s.score * 100)}%</span>` : "";
  return `<div class="card">
    <div class="card-title">${esc(s.id)} ${statusPill(s.status)} ${vettingPill(s.vetting)} <span class="muted">${esc(s.origin)} · ${esc(s.pin)}</span>${score}</div>
    <div class="card-desc">${esc(s.guidance)}</div>
    <div class="chips">${phaseChips(s.phases)}</div>
  </div>`;
}

async function load() {
  try {
    const personas = (await api("/v1/personas")).items;
    $("personas").innerHTML = personas.map(personaCard).join("") || "<p class='muted'>No personas.</p>";

    const skills = (await api("/v1/skills")).items;
    $("skills").innerHTML = skills.map(skillCard).join("") || "<p class='muted'>No skills.</p>";
  } catch (e) { /* a 401 already redirected */ }
}

load();
