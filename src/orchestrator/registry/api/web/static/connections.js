const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

let STATE = { writable: false, config_path: "" };

function cfgParam() {
  const v = $("config").value.trim();
  return v ? "?config=" + encodeURIComponent(v) : "";
}

function toolRow(t) {
  const ro = t.read_only === true ? '<span class="pill ok">read-only</span>'
    : (t.read_only === false ? '<span class="pill skip">write</span>' : "");
  return `<tr><td><code>${esc(t.name)}</code> ${ro}</td><td class="muted">${esc(t.description)}</td></tr>`;
}

function serverCard(s) {
  const statusPill = s.reachable ? '<span class="pill ok">reachable</span>'
    : `<span class="pill fail">${esc(s.error || "unreachable")}</span>`;
  const allow = s.allow ? `<span class="muted">allow: ${s.allow.map(esc).join(", ")}</span>`
    : '<span class="muted">allow: all</span>';
  const write = s.write_enabled ? ' <span class="pill skip">write-enabled</span>' : "";
  const tools = s.tools && s.tools.length
    ? `<table class="mini"><tbody>${s.tools.map(toolRow).join("")}</tbody></table>`
    : (s.reachable ? "<p class='muted'>No allow-listed tools.</p>" : "");
  const remove = STATE.writable
    ? `<a href="#" class="remove" data-name="${esc(s.name)}">remove</a>` : "";
  return `<div class="card">
    <div class="card-title">${esc(s.name)} ${statusPill}${write} <span class="run-actions">${remove}</span></div>
    <div class="card-desc"><span class="muted">${esc(s.transport)}</span> <code>${esc(s.target)}</code> · ${allow}</div>
    ${tools}
  </div>`;
}

function checkPill(c) {
  if (!c.passed) return '<span class="pill fail">missing</span>';
  return c.optional ? '<span class="pill skip">not set</span>' : '<span class="pill ok">configured</span>';
}

function editorHtml() {
  if (!STATE.writable) {
    return `<div class="empty">Editing is off. Set <code>ORCHESTRATOR_MCP_CONFIG_WRITABLE=1</code> to add servers here, or edit <code>${esc(STATE.config_path)}</code> directly.</div>`;
  }
  return `<div class="panel"><div class="panel-head">Add / update a server</div>
    <p class="panel-sub">Writes <code>${esc(STATE.config_path)}</code>. A <strong>stdio</strong> server's command runs on this machine.</p>
    <div class="repo-bar">
      <label>name <input id="sv-name" size="14"></label>
      <label>type <select id="sv-type"><option value="command">stdio (command)</option><option value="url">http (url)</option></select></label>
      <label><span id="sv-tgt-label">command</span> <input id="sv-target" size="30" placeholder="orchestrator-mcp"></label>
    </div>
    <div class="repo-bar">
      <label>args <input id="sv-args" size="20" placeholder="space-separated"></label>
      <label>allow <input id="sv-allow" size="20" placeholder="tool names (blank = all)"></label>
      <button id="sv-save" class="primary">Save server</button>
    </div>
    <div id="sv-status" class="muted"></div></div>`;
}

async function save() {
  const name = $("sv-name").value.trim();
  if (!name) { $("sv-status").innerHTML = '<span class="err">Name is required.</span>'; return; }
  const type = $("sv-type").value;
  const target = $("sv-target").value.trim();
  const args = $("sv-args").value.trim() ? $("sv-args").value.trim().split(/\s+/) : [];
  const allow = $("sv-allow").value.trim() ? $("sv-allow").value.trim().split(/[\s,]+/) : null;
  const cfg = $("config").value.trim() || null;
  const bodyObj = { name, args, allow, enabled: true, config: cfg };
  bodyObj[type] = target;
  $("sv-status").textContent = "Saving…";
  try {
    await window.spine.apiJSON("/v1/connections/servers", { method: "POST", body: JSON.stringify(bodyObj) });
    $("sv-status").innerHTML = '<span class="ok">Saved.</span>';
    load();
  } catch (e) {
    if (String(e.message) !== "session expired") $("sv-status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

async function remove(name) {
  if (!window.confirm(`Remove MCP server "${name}"?`)) return;
  try {
    await fetch("/v1/connections/servers/" + encodeURIComponent(name) + cfgParam(), { method: "DELETE" })
      .then((r) => { if (r.status === 401) { window.location = "/login"; } });
    load();
  } catch (e) { /* ignore */ }
}

async function load() {
  $("status").textContent = "Testing…";
  $("mcp").innerHTML = "<p class='muted'>Testing…</p>";
  try {
    const d = await window.spine.apiJSON("/v1/connections" + cfgParam());
    STATE = { writable: d.writable, config_path: d.config_path };
    $("confbar").innerHTML = `config: <code>${esc(d.config_path)}</code>${d.mcp_config_present ? "" : " (not found)"} · editing ${d.writable ? '<span class="pill ok">on</span>' : '<span class="pill skip">off</span>'}`;
    $("mcp").innerHTML = d.servers && d.servers.length
      ? d.servers.map(serverCard).join("")
      : `<div class="empty">${d.mcp_config_present ? "No MCP servers in the config." : "No config file yet."}</div>`;
    $("editor").innerHTML = editorHtml();
    if (STATE.writable) {
      $("sv-save").onclick = save;
      $("sv-type").onchange = () => { $("sv-tgt-label").textContent = $("sv-type").value === "url" ? "url" : "command"; };
    }
    $("mcp").querySelectorAll("a.remove").forEach((a) =>
      a.onclick = (e) => { e.preventDefault(); remove(a.dataset.name); });
    $("sources").innerHTML = "<table class='mini'><tbody>"
      + d.sources.map((c) => `<tr><td>${esc(c.name)}</td><td>${checkPill(c)}</td><td class="muted">${esc(c.detail)}</td></tr>`).join("")
      + "</tbody></table>";
    $("status").innerHTML = `<span class="ok">${(d.servers || []).filter((s) => s.reachable).length}/${(d.servers || []).length} MCP server(s) reachable.</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

// --- server-side file picker: choose an mcp.json from the server's disk ---
function fsRow(e) {
  const icon = e.is_dir ? "📁" : "📄";
  const match = !e.is_dir && /mcp.*\.json$/i.test(e.name) ? " fs-match" : "";
  return `<div class="fs-entry${match}" data-path="${esc(e.path)}" data-dir="${e.is_dir}">${icon} ${esc(e.name)}</div>`;
}

function closePicker() { $("fsmodal").innerHTML = ""; }

function selectFile(path) { $("config").value = path; closePicker(); load(); }

async function fsList(path) {
  try {
    const d = await window.spine.apiJSON("/v1/fs/list" + (path ? "?path=" + encodeURIComponent(path) : ""));
    const up = d.parent ? '<a href="#" id="fs-up">↑ up</a>' : '<span class="muted">↑ up</span>';
    const rows = d.entries.map(fsRow).join("") || "<p class='muted'>Empty.</p>";
    $("fsmodal").innerHTML = `<div class="fs-backdrop"><div class="fs-modal">
      <div class="fs-head"><strong>Select an mcp.json</strong><a href="#" id="fs-close">✕</a></div>
      <div class="fs-bar">${up} · <a href="#" id="fs-home">~ home</a> · <a href="#" id="fs-ws">workspace</a>
        <div class="fs-path"><code>${esc(d.path)}</code></div></div>
      <div class="fs-list">${rows}${d.truncated ? "<p class='muted'>…truncated.</p>" : ""}</div>
    </div></div>`;
    $("fs-close").onclick = (e) => { e.preventDefault(); closePicker(); };
    if (d.parent) $("fs-up").onclick = (e) => { e.preventDefault(); fsList(d.parent); };
    $("fs-home").onclick = (e) => { e.preventDefault(); fsList("~"); };
    $("fs-ws").onclick = (e) => { e.preventDefault(); fsList(""); };
    $("fsmodal").querySelector(".fs-backdrop").onclick = (ev) => {
      if (ev.target.classList.contains("fs-backdrop")) closePicker();
    };
    $("fsmodal").querySelectorAll(".fs-entry").forEach((el) => {
      el.onclick = () => (el.dataset.dir === "true" ? fsList(el.dataset.path) : selectFile(el.dataset.path));
    });
  } catch (e) {
    if (String(e.message) !== "session expired") window.alert("Cannot list directory: " + e.message);
  }
}

$("browse").onclick = () => fsList("");
$("reload").onclick = load;
load();
