const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

$("run").onclick = async () => {
  const repo = $("repo").value.trim() || ".";
  const lens = $("lens").value;
  $("run").disabled = true;
  $("report").innerHTML = "";
  $("status").textContent = `Generating (${lens} lens)…`;
  try {
    const job = await window.spine.runJob("/v1/capabilities/state", { repo, lens }, (m) => { $("status").textContent = m; });
    const md = await window.spine.fetchArtifact(job.job_id);
    $("report").innerHTML = window.renderMarkdown(md);
    $("status").innerHTML = `<span class="ok">Rendered ${esc(lens)} lens.</span>`;
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally {
    $("run").disabled = false;
  }
};
