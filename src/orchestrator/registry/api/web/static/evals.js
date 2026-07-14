const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

function skillCard(s) {
  const score = s.score != null
    ? `<span class="pill ok">held-out ${Math.round(s.score * 100)}%</span>`
    : '<span class="muted">not yet measured</span>';
  const phases = (s.phases || []).map((p) => `<span class="phase">${esc(p)}</span>`).join(" ");
  return `<div class="card">
    <div class="card-title">${esc(s.id)} <span class="pill vet-${esc(s.vetting)}">${esc(s.vetting)}</span> ${score}</div>
    <div class="card-desc">${esc(s.guidance)}</div>
    ${phases ? `<div class="chips">${phases}</div>` : ""}
  </div>`;
}

async function load() {
  $("status").textContent = "Loading…";
  try {
    const skills = (await window.spine.apiJSON("/v1/skills")).items || [];
    const active = skills.filter((s) => s.status === "active");
    const candidate = skills.filter((s) => s.status !== "active");
    $("active").innerHTML = active.map(skillCard).join("") || "<p class='muted'>No promoted skills yet.</p>";
    $("candidate").innerHTML = candidate.map(skillCard).join("") || "<p class='muted'>None.</p>";
    $("status").innerHTML = `<span class="ok">${active.length} active · ${candidate.length} candidate.</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

load();
