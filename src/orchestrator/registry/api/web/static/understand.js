const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function logLine(msg) {
  const p = $("progress");
  p.innerHTML += `<div class="pline">${esc(msg)}</div>`;
  p.scrollTop = p.scrollHeight;
}

$("run").onclick = async () => {
  const repo = $("repo").value.trim() || ".";
  $("run").disabled = true;
  $("progress").innerHTML = "";
  $("result").innerHTML = "";
  $("status").textContent = "Starting…";
  try {
    const job = await window.spine.runJob("/v1/capabilities/understand", { repo }, logLine);
    const s = job.summary || {};
    const files = (s.files || []).map((f) => `<span class="chip">${esc(f)}</span>`).join("");
    $("status").innerHTML = `<span class="ok">Done${s.greenfield ? " · greenfield repo" : ""}.</span>`;
    $("result").innerHTML = `<div class="panel"><div class="panel-head">Episteme built</div>
      <p class="panel-sub">${esc(s.summary || "Code mapped into episteme/*.md.")}</p>
      <div class="chips">${files}</div>
      <div class="cards" style="margin-top:1rem">
        <a class="card" href="/app/memory-bank">Browse episteme →</a>
        <a class="card" href="/app/state">Current-state report →</a>
        <a class="card" href="/app/graph">Knowledge graph →</a>
      </div></div>`;
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally {
    $("run").disabled = false;
  }
};
