const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

function intentCard(i) {
  return `<div class="card"><div class="card-title">${esc(i.title || i.id || "intent")}</div>
    <div class="card-desc">${esc(i.summary || "")}</div></div>`;
}

function specCard(s) {
  const ac = (s.acceptance_criteria && s.acceptance_criteria.length)
    ? `<ul class="plan-list">${s.acceptance_criteria.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : "";
  return `<div class="card"><div class="card-title">${esc(s.title)} <span class="muted">${esc(s.estimate || "")}</span></div>
    <div class="card-desc">${esc(s.summary)}</div>${ac}</div>`;
}

async function preview() {
  const source = $("source").value.trim();
  if (!source) { $("pstatus").innerHTML = '<span class="err">Enter a source.</span>'; return; }
  $("pstatus").textContent = "Analyzing…";
  $("backlog").innerHTML = "";
  try {
    const d = await window.spine.apiJSON("/v1/intake/preview", { method: "POST", body: JSON.stringify({ source }) });
    const intents = (d.intents || []).map(intentCard).join("");
    const gaps = (d.gaps || []).map((g) =>
      `<tr><td><span class="pill ${g.severity === "error" ? "fail" : "skip"}">${esc(g.severity)}</span></td><td class="muted">${esc(g.rule)}</td><td>${esc(g.message)}</td></tr>`).join("");
    const specs = (d.specs || []).map(specCard).join("");
    $("backlog").innerHTML =
      `<p class="muted">${esc(d.documents)} document(s)${d.truncated ? " · truncated" : ""}${d.blocked ? " · blocked" : ""}</p>`
      + `<h3>Intents (${(d.intents || []).length})</h3>${intents || "<p class='muted'>None.</p>"}`
      + (gaps ? `<h3>Gaps</h3><table class="mini"><tbody>${gaps}</tbody></table>` : "")
      + `<h3>Draft specs (${(d.specs || []).length})</h3>${specs || "<p class='muted'>None.</p>"}`;
    $("pstatus").innerHTML = '<span class="ok">Previewed.</span>';
  } catch (e) {
    if (String(e.message) !== "session expired") $("pstatus").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

async function delegate() {
  const source = $("source").value.trim();
  if (!source) { $("dstatus").innerHTML = '<span class="err">Enter a source above.</span>'; return; }
  const createJira = $("create_jira").checked;
  if (createJira && !window.confirm("Create REAL Jira issues for this source?")) return;
  $("dstatus").textContent = "Starting…";
  $("runout").innerHTML = "";
  try {
    const r = await window.spine.apiJSON("/v1/runs/start", { method: "POST", body: JSON.stringify({ source, create_jira: createJira }) });
    const gates = Object.entries(r.gates || {}).map(([k, v]) => `${esc(k)} <code>${esc(v)}</code>`).join(" · ");
    $("runout").innerHTML = `<div class="panel"><div class="panel-head">Run started</div>
      <p class="panel-sub">id <code>${esc(r.sdlc_id)}</code> · queue ${esc(r.task_queue)}</p>
      <p class="muted">Gates: ${gates}</p>
      <div class="cards"><a class="card" href="/app/inbox">Track in Inbox →</a><a class="card" href="/app/governance">Governance →</a></div></div>`;
    $("dstatus").innerHTML = '<span class="ok">Delegated.</span>';
  } catch (e) {
    if (String(e.message) === "session expired") return;
    const msg = /HTTP 503/.test(e.message) ? "Temporal worker not reachable — is it up?" : esc(e.message);
    $("dstatus").innerHTML = `<span class="err">${msg}</span>`;
  }
}

$("preview").onclick = preview;
$("delegate").onclick = delegate;
