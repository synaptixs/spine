const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function checkPill(c) {
  if (!c.passed) return `<span class="pill fail">fail</span>`;
  if (c.optional) return `<span class="pill skip">skipped</span>`;
  return `<span class="pill ok">ok</span>`;
}

async function load() {
  try {
    const r = await api("/v1/system/readiness");
    const banner = r.ok
      ? `<div class="banner ok">All required checks pass — the stack is ready.</div>`
      : `<div class="banner fail">Not ready — one or more required checks are failing.</div>`;
    const db = `<div class="card"><div class="card-title">Database ${r.db_ready ? '<span class="pill ok">ok</span>' : '<span class="pill fail">fail</span>'}</div>
      <div class="card-desc">${esc(r.db_detail)}</div></div>`;
    const rows = r.checks.map((c) =>
      `<tr><td>${esc(c.name)}</td><td>${checkPill(c)}</td><td class="muted">${esc(c.detail)}</td></tr>`).join("");
    $("readiness").innerHTML = banner + db
      + `<h2>Environment</h2><table class="mini"><thead><tr><th>group</th><th>status</th><th>detail</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("readiness").innerHTML = `<p class="muted">Could not load readiness.</p>`;
  }
}

load();
