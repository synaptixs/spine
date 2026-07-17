const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const KINDS = { loop: "Agentic loop", governance: "Governance", memory: "Memory", spine: "Semantic spine" };

function featureRow(f) {
  const pill = f.enabled ? '<span class="pill ok">on</span>' : '<span class="pill skip">off</span>';
  return `<div class="card">
    <div class="card-title">${esc(f.name)} ${pill} <span class="muted"><code>${esc(f.key)}</code></span></div>
    <div class="card-desc">${esc(f.detail)}</div></div>`;
}

async function load() {
  $("status").textContent = "Loading…";
  try {
    const features = (await window.spine.apiJSON("/v1/system/advanced")).features || [];
    const groups = {};
    for (const f of features) (groups[f.kind] = groups[f.kind] || []).push(f);
    let html = "";
    for (const kind of Object.keys(KINDS)) {
      if (!groups[kind]) continue;
      html += `<h2>${esc(KINDS[kind])}</h2>` + groups[kind].map(featureRow).join("");
    }
    $("features").innerHTML = html || "<p class='muted'>No advanced features.</p>";
    const on = features.filter((f) => f.enabled).length;
    $("status").innerHTML = `<span class="ok">${on}/${features.length} enabled.</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

load();
