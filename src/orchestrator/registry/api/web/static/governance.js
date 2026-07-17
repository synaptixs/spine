const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function tile(v, l) { return `<div class="tile"><div class="tile-v">${esc(v)}</div><div class="tile-l">${esc(l)}</div></div>`; }

function spendPanel(s) {
  const cap = s.budget_cap_usd || 0;
  const pct = cap > 0 ? Math.min(100, Math.round((100 * s.tool_cost_usd) / cap)) : 0;
  const barCls = s.over_cap ? "over" : "";
  const breaches = (s.breaches || []).map((b) =>
    `<tr><td class="muted">${esc(b.stage)}</td><td>$${esc(b.spent_usd)}</td><td class="muted">cap $${esc(b.max_cost_usd)}</td><td class="muted">${esc((b.at || "").slice(0, 19))}</td></tr>`).join("");
  return `<h2>Spend</h2>
    <div class="tiles">${tile("$" + (s.tool_cost_usd || 0), "tool cost")}${tile(s.tool_calls || 0, "tool calls")}${tile("$" + cap, "budget cap")}</div>
    <div class="bars"><div class="bar-row"><span class="bar-l">of cap</span><span class="bar"><span class="${barCls}" style="width:${pct}%"></span></span><span class="bar-v">${pct}%</span></div></div>
    ${s.over_cap ? '<p class="err">Recorded tool cost exceeds the configured cap.</p>' : ""}
    ${breaches ? `<h3>Budget breaches</h3><table class="mini"><tbody>${breaches}</tbody></table>` : ""}`;
}

function listPanel(title, items, render) {
  if (!items || !items.length) return `<h2>${esc(title)}</h2><p class="muted">None recorded.</p>`;
  return `<h2>${esc(title)}</h2><table class="mini"><tbody>${items.map(render).join("")}</tbody></table>`;
}

function outcomeClass(o) { return o === "PASS" ? "ok" : (o === "FAIL" ? "fail" : "skip"); }

async function load() {
  const runId = $("run_id").value.trim();
  if (!runId) { $("status").innerHTML = '<span class="err">Enter a run id.</span>'; return; }
  $("status").textContent = "Looking up…";
  $("gov").innerHTML = "";
  $("export").style.display = "none";
  try {
    const g = await window.spine.apiJSON("/v1/audit/" + encodeURIComponent(runId) + "/governance");
    $("gov").innerHTML =
      spendPanel(g.spend || {})
      + listPanel("Policy (output verifier)", g.policy, (p) =>
        `<tr><td><span class="pill ${outcomeClass(p.outcome)}">${esc(p.outcome)}</span></td><td>${(p.rules || []).map((r) => `<span class="chip">${esc(r)}</span>`).join(" ") || "<span class='muted'>no rule hits</span>"}</td><td class="muted">${esc((p.at || "").slice(0, 19))}</td></tr>`)
      + listPanel("Approvals", g.approvals, (a) =>
        `<tr><td>${esc(a.action)}</td><td class="muted">${esc(a.actor)}</td><td class="muted">${esc((a.at || "").slice(0, 19))}</td></tr>`)
      + `<p class="muted note">${esc(g.note)}</p>`;
    const ex = $("export");
    ex.href = "/v1/audit/" + encodeURIComponent(runId) + "/export";
    ex.style.display = "inline";
    $("status").innerHTML = `<span class="ok">Loaded.</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") {
      $("status").innerHTML = `<span class="err">${/HTTP 404/.test(e.message) ? "No such run." : esc(e.message)}</span>`;
    }
  }
}

$("load").onclick = load;
$("run_id").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
