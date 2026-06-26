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

async function load() {
  try {
    const personas = (await api("/v1/personas")).items;
    $("personas").innerHTML = personas.map((p) =>
      `<div class="card"><div class="card-title">${esc(p.id)} <span class="muted">v${esc(p.version)} · ${esc(p.workflow_slot)} · ${esc(p.model)}</span></div>
         <div class="card-desc">${esc(p.role)}</div>
         <div class="chips">${p.skills.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>
       </div>`).join("") || "<p class='muted'>No personas.</p>";

    const skills = (await api("/v1/skills")).items;
    $("skills").innerHTML = skills.map((s) => {
      const score = s.score != null ? ` <span class="muted">· held-out ${Math.round(s.score * 100)}%</span>` : "";
      return `<div class="card"><div class="card-title">${esc(s.id)} ${statusPill(s.status)} ${vettingPill(s.vetting)} <span class="muted">${esc(s.origin)}</span>${score}</div>
         <div class="card-desc">${esc(s.guidance)}</div>
         <div class="chips">${phaseChips(s.phases)}</div></div>`;
    }).join("") || "<p class='muted'>No skills.</p>";
  } catch (e) { /* a 401 already redirected */ }
}

load();
