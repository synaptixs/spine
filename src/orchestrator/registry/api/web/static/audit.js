const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function qs() {
  const p = new URLSearchParams();
  for (const k of ["run_id", "actor", "action", "resource_type"]) {
    const v = $(k).value.trim();
    if (v) p.set(k, v);
  }
  p.set("limit", "200");
  return p.toString();
}

function rowHtml(r, i) {
  const after = r.after && Object.keys(r.after).length ? JSON.stringify(r.after, null, 2) : "";
  const detail = after
    ? `<tr id="ad-${i}" class="ad" hidden><td colspan="5"><pre>${esc(after)}</pre></td></tr>`
    : "";
  return `<tr class="clickable" data-i="${i}">
    <td class="muted">${esc((r.timestamp || "").slice(0, 19))}</td>
    <td>${esc(r.action)}</td>
    <td class="muted">${esc(r.resource_type)}</td>
    <td><code>${esc(r.resource_id)}</code></td>
    <td class="muted">${esc(r.actor)}</td></tr>${detail}`;
}

async function load() {
  $("status").textContent = "Searching…";
  try {
    const data = await window.spine.apiJSON("/v1/audit?" + qs());
    const box = $("rows");
    if (!data.items.length) { box.innerHTML = "<div class='empty'>No matching audit events.</div>"; $("status").textContent = ""; return; }
    box.innerHTML = "<table class='mini'><thead><tr><th>time</th><th>action</th><th>type</th><th>resource</th><th>actor</th></tr></thead><tbody>"
      + data.items.map(rowHtml).join("") + "</tbody></table>";
    box.querySelectorAll("tr.clickable").forEach((tr) => {
      const d = $("ad-" + tr.dataset.i);
      if (d) tr.onclick = () => { d.hidden = !d.hidden; };
    });
    $("status").innerHTML = `<span class="ok">${data.items.length} event(s).</span>`;
  } catch (e) {
    if (String(e.message) !== "session expired") $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

$("load").onclick = load;
load();
