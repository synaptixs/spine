const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };

let files = [];

function show(idx) {
  const f = files[idx];
  if (!f) return;
  $("mb-doc").innerHTML = window.renderMarkdown(f.markdown);
  document.querySelectorAll("#mb-nav a").forEach((a, i) => a.classList.toggle("active", i === idx));
}

async function load() {
  const repo = $("repo").value.trim() || ".";
  $("status").textContent = "Loading…";
  $("mb-nav").innerHTML = "";
  $("mb-doc").innerHTML = "";
  try {
    const data = await window.spine.apiJSON("/v1/capabilities/memory-bank?repo=" + encodeURIComponent(repo));
    if (!data.exists || !data.files.length) {
      $("status").innerHTML = "";
      $("mb-doc").innerHTML = "<div class='empty'>No episteme yet for this repo. Run <a href='/app/understand'>Understand</a> first.</div>";
      return;
    }
    files = data.files;
    $("mb-nav").innerHTML = files.map((f, i) => `<a href="#" data-i="${i}">${esc(f.name)}</a>`).join("");
    $("mb-nav").querySelectorAll("a").forEach((a) => { a.onclick = (e) => { e.preventDefault(); show(+a.dataset.i); }; });
    $("status").innerHTML = `<span class="ok">${files.length} file(s).</span>`;
    show(0);
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

$("load").onclick = load;
load();
