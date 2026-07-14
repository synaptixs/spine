const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

function tile(v, l) { return `<div class="tile"><div class="tile-v">${esc(v)}</div><div class="tile-l">${esc(l)}</div></div>`; }
function tiles(s) { return `<div class="tiles">${tile(s.nodes || 0, "nodes")}${tile(s.grounded_nodes || 0, "grounded")}${tile(s.external_nodes || 0, "external")}${tile(s.edges || 0, "edges")}</div>`; }

function bars(obj) {
  const entries = Object.entries(obj || {});
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return entries.map(([k, v]) =>
    `<div class="bar-row"><span class="bar-l">${esc(k)}</span><span class="bar"><span style="width:${Math.round((100 * v) / max)}%"></span></span><span class="bar-v">${esc(v)}</span></div>`).join("");
}

function moduleTable(mods) {
  const max = Math.max(1, ...mods.map((m) => m.nodes));
  return `<table class="mini"><thead><tr><th>module</th><th>nodes</th><th></th></tr></thead><tbody>`
    + mods.map((m) => `<tr><td><code>${esc(m.module)}</code></td><td>${esc(m.nodes)}</td><td class="tcell"><span class="bar"><span style="width:${Math.round((100 * m.nodes) / max)}%"></span></span></td></tr>`).join("")
    + `</tbody></table>`;
}

function edgeTable(edges) {
  if (!edges.length) return "<p class='muted'>No cross-module edges.</p>";
  return `<table class="mini"><thead><tr><th>from</th><th></th><th>to</th><th>kind</th><th>count</th></tr></thead><tbody>`
    + edges.map((e) => `<tr><td><code>${esc(e.src)}</code></td><td class="muted">→</td><td><code>${esc(e.dst)}</code></td><td class="muted">${esc(e.kind)}</td><td>${esc(e.count)}</td></tr>`).join("")
    + `</tbody></table>`;
}

function symbolTable(syms) {
  if (!syms.length) return "<p class='muted'>None.</p>";
  return `<table class="mini"><thead><tr><th>symbol</th><th>kind</th><th>module</th><th>degree</th></tr></thead><tbody>`
    + syms.map((s) => `<tr><td>${esc(s.name)}</td><td class="muted">${esc(s.kind)}</td><td class="muted"><code>${esc(s.module || "")}</code></td><td>${esc(s.degree)}</td></tr>`).join("")
    + `</tbody></table>`;
}

function render(o) {
  const t = o.truncated || {}, tot = o.totals || {};
  const modNote = t.modules ? ` <span class="muted">(top ${(o.modules || []).length} of ${esc(tot.modules)})</span>` : "";
  const edgeNote = t.module_edges ? ` <span class="muted">(top ${(o.module_edges || []).length} of ${esc(tot.module_edges)})</span>` : "";
  $("graph").innerHTML =
    tiles(o.summary || {})
    + `<h2>Node kinds</h2><div class="bars">${bars(o.kinds)}</div>`
    + `<h2>Edge kinds</h2><div class="chips">${Object.entries(o.edge_kinds || {}).map(([k, v]) => `<span class="chip">${esc(k)} · ${esc(v)}</span>`).join("")}</div>`
    + `<h2>Modules${modNote}</h2>${moduleTable(o.modules || [])}`
    + `<h2>Module dependencies${edgeNote}</h2>${edgeTable(o.module_edges || [])}`
    + `<h2>Most-connected symbols</h2>${symbolTable(o.top_symbols || [])}`;
}

function logLine(msg) { const p = $("progress"); p.innerHTML += `<div class="pline">${esc(msg)}</div>`; p.scrollTop = p.scrollHeight; }

$("run").onclick = async () => {
  const repo = $("repo").value.trim() || ".";
  $("run").disabled = true;
  $("progress").innerHTML = "";
  $("graph").innerHTML = "";
  $("status").textContent = "Extracting…";
  try {
    const job = await window.spine.runJob("/v1/capabilities/pkg/extract", { repo }, logLine);
    const o = JSON.parse(await window.spine.fetchArtifact(job.job_id));
    render(o);
    const s = o.summary || {};
    $("status").innerHTML = `<span class="ok">${esc(s.nodes || 0)} nodes · ${esc(s.edges || 0)} edges.</span>`;
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally {
    $("run").disabled = false;
  }
};
