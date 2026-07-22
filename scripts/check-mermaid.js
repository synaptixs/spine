// Verify every ```mermaid block in a markdown file actually renders through Spine's OWN
// renderer (web/static/md.js), instead of silently falling back to a <pre> code block.
//
// Why this exists: `md.js` ships a ~90-line hand-rolled `mermaidSvg()` (no build step, must
// work air-gapped) that supports only a small subset — `flowchart LR|TD|TB|RL`, quoted node
// declarations `id["label"]`, `subgraph x["Zone"]`/`end`, and bare-id edges `a --> b` /
// `a -->|label| b`. Anything outside it (chained `a --> b --> c`, dotted `-. x .->`,
// decision `c{...}`, or a node declared inline in an edge line) makes the whole block fall
// back to <pre>. GitHub renders those diagrams fine, so a broken one is invisible until you
// open our own web UI — which is how five diagrams sat broken in KNOWLEDGE_GRAPH.md.
//
//     node scripts/check-mermaid.js *.md
//
// Exits non-zero if any block falls back, so it can gate a commit or CI step.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const MD_JS = path.join(__dirname, "..", "src", "orchestrator", "registry", "api", "web", "static", "md.js");

const targets = process.argv.slice(2);
if (!targets.length) {
  console.error("usage: node scripts/check-mermaid.js <file.md> [...]");
  process.exit(2);
}

const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(MD_JS, "utf8"), sandbox);
const render = sandbox.window.renderMarkdown;
if (typeof render !== "function") {
  console.error(`FAIL: ${MD_JS} did not expose window.renderMarkdown`);
  process.exit(2);
}

let fail = 0;
let total = 0;
for (const target of targets) {
  const text = fs.readFileSync(target, "utf8");
  const blocks = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1]);
  blocks.forEach((body, i) => {
    total++;
    const ok = render("```mermaid\n" + body + "```").includes('<figure class="mermaid"><svg');
    if (!ok) fail++;
    const head = (body.split("\n").find((l) => l.trim()) || "").trim().slice(0, 40);
    console.log(`  ${ok ? "ok      " : "FALLBACK"}  ${target} [block ${i + 1}] ${head}`);
  });
}

if (!total) {
  console.log("no mermaid blocks found");
  process.exit(0);
}
console.log(`\n${total - fail}/${total} render as inline SVG${fail ? ` — ${fail} FELL BACK to <pre>` : ""}`);
process.exit(fail ? 1 : 0);
