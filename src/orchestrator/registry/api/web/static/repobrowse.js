// Shared repo-folder picker for the Understand section.
//
// Adds a native-feel "Browse…" button next to the repo input on every
// intelligence page (understand / state / memory-bank / graph / catalog) so a
// user can pick a local repo *directory* off the server's disk without typing
// the path — the same exposure the Connections page has for picking an
// mcp.json file. Git URLs (github/bitbucket/gitlab/enterprise) are still typed
// straight into the input; this only helps with local paths.
//
// Wrapped in an IIFE: the page-specific script (understand.js, state.js, …)
// declares its own top-level `const $` / `const esc`, so ours must stay local
// or the classic-script global scope would collide.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  };
  const modal = () => $("fsmodal");
  function close() {
    if (modal()) modal().innerHTML = "";
  }

  // Directories navigate on click; files are shown greyed and inert (this is a
  // folder picker). A `.git` entry marks the folder as a git repo.
  function row(e) {
    if (!e.is_dir) return `<div class="fs-entry fs-file">📄 ${esc(e.name)}</div>`;
    const git = e.name === ".git" ? " fs-match" : "";
    return `<div class="fs-entry${git}" data-path="${esc(e.path)}" data-dir="true">📁 ${esc(e.name)}</div>`;
  }

  async function list(path) {
    let d;
    try {
      d = await window.spine.apiJSON("/v1/fs/list" + (path ? "?path=" + encodeURIComponent(path) : ""));
    } catch (e) {
      if (String(e.message) !== "session expired") window.alert("Cannot list directory: " + e.message);
      return;
    }
    const isRepo = d.entries.some((e) => e.is_dir && e.name === ".git");
    const up = d.parent ? '<a href="#" id="fs-up">↑ up</a>' : '<span class="muted">↑ up</span>';
    const rows = d.entries.map(row).join("") || "<p class='muted'>Empty.</p>";
    const badge = isRepo ? " <span class='fs-git'>git repo</span>" : "";
    modal().innerHTML = `<div class="fs-backdrop"><div class="fs-modal">
      <div class="fs-head"><strong>Select a repo folder</strong><a href="#" id="fs-close">✕</a></div>
      <div class="fs-bar">${up} · <a href="#" id="fs-home">~ home</a> · <a href="#" id="fs-ws">workspace</a>
        <div class="fs-path"><code>${esc(d.path)}</code>${badge}</div></div>
      <div class="fs-list">${rows}${d.truncated ? "<p class='muted'>…truncated.</p>" : ""}</div>
      <div class="fs-foot"><button id="fs-use" type="button" class="primary">Use this folder</button>
        <span class="muted">${esc(d.path)}</span></div>
    </div></div>`;
    $("fs-close").onclick = (e) => {
      e.preventDefault();
      close();
    };
    if (d.parent)
      $("fs-up").onclick = (e) => {
        e.preventDefault();
        list(d.parent);
      };
    $("fs-home").onclick = (e) => {
      e.preventDefault();
      list("~");
    };
    $("fs-ws").onclick = (e) => {
      e.preventDefault();
      list("");
    };
    $("fs-use").onclick = () => {
      $("repo").value = d.path;
      close();
      $("status").textContent = "Repo set to " + d.path;
    };
    modal().querySelector(".fs-backdrop").onclick = (ev) => {
      if (ev.target.classList.contains("fs-backdrop")) close();
    };
    modal()
      .querySelectorAll(".fs-entry[data-dir='true']")
      .forEach((el) => {
        el.onclick = () => list(el.dataset.path);
      });
  }

  const btn = $("repo-browse");
  if (btn) btn.onclick = () => list("");
})();
