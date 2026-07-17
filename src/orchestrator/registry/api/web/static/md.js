// Minimal, dependency-free Markdown → HTML renderer for the deterministic reports
// Spine produces (current-state, memory-bank). Handles headings, lists, tables,
// fenced code, inline code/bold/italic, links, and rules — enough for our own
// output, not a general CommonMark engine. Exposes window.renderMarkdown.
(function () {
  function esc(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  function inline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    // Links — only http(s), root-relative, or anchor targets (no javascript: etc.).
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, h) =>
      /^(https?:\/\/|\/|#)/.test(h) ? `<a href="${h}" rel="noopener">${t}</a>` : t);
    return s;
  }

  function splitRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  }

  // --- mermaid ---------------------------------------------------------------
  // Spine generates its own flowcharts, so the grammar here is a tiny known subset:
  // `flowchart LR`, optional `subgraph z["Zone"]`/`end`, `id["label<br/>more"]`, and
  // `a --> b` / `a -->|12| b`. That makes a ~90-line renderer a better trade than a
  // 2.6MB library — this frontend has no build step, ships inside the pip wheel (the
  // whole existing UI is ~100KB), and must work air-gapped, which rules out a CDN.
  // Anything outside the subset falls back to the original <pre>: no picture beats a
  // wrong picture.
  const NODE_W = 190, NODE_H = 46, COL_GAP = 90, ROW_GAP = 18, PAD = 12, LINE_H = 14;

  function parseMermaid(src) {
    const lines = src.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!/^flowchart\s+(LR|TD|TB|RL)$/.test(lines[0] || "")) return null;
    const nodes = new Map(), edges = [];
    let zone = null;
    for (const line of lines.slice(1)) {
      let m;
      if ((m = line.match(/^subgraph\s+\S+\["(.*)"\]$/))) { zone = m[1]; continue; }
      if (line === "end") { zone = null; continue; }
      if ((m = line.match(/^([A-Za-z0-9_]+)\["(.*)"\]$/))) {
        nodes.set(m[1], { id: m[1], label: m[2], zone });
        continue;
      }
      if ((m = line.match(/^([A-Za-z0-9_]+)\s*-->(?:\|(.*?)\|)?\s*([A-Za-z0-9_]+)$/))) {
        edges.push({ from: m[1], to: m[3], label: m[2] || "" });
        continue;
      }
      return null; // unknown construct — don't guess
    }
    if (!nodes.size) return null;
    for (const e of edges) if (!nodes.has(e.from) || !nodes.has(e.to)) return null;
    return { nodes, edges };
  }

  // Longest-path layering. Relaxation is capped at node count so a dependency cycle
  // (two areas importing each other — a real case) settles instead of spinning.
  // Columns are then re-indexed to a dense 0..k-1: a cycle ratchets the raw indices
  // upward (a 2-node cycle lands on {2,3}), and positioning by a sparse index while
  // sizing by the column *count* pushes nodes outside the viewBox, where they clip.
  function layer(g) {
    const col = new Map([...g.nodes.keys()].map((k) => [k, 0]));
    for (let pass = 0; pass < g.nodes.size; pass++) {
      let moved = false;
      for (const e of g.edges) {
        const want = col.get(e.from) + 1;
        if (col.get(e.to) < want) { col.set(e.to, want); moved = true; }
      }
      if (!moved) break;
    }
    const dense = new Map([...new Set([...col.values()])].sort((a, b) => a - b).map((v, k) => [v, k]));
    for (const [id, c] of col) col.set(id, dense.get(c));
    return col;
  }

  function mermaidSvg(src) {
    const g = parseMermaid(src);
    if (!g) return null;
    const col = layer(g);
    const cols = new Map();
    for (const [id, c] of col) { if (!cols.has(c)) cols.set(c, []); cols.get(c).push(id); }
    const pos = new Map();
    let maxRows = 0;
    for (const [c, ids] of [...cols].sort((a, b) => a[0] - b[0])) {
      ids.sort();
      maxRows = Math.max(maxRows, ids.length);
      ids.forEach((id, r) => pos.set(id, {
        x: PAD + c * (NODE_W + COL_GAP),
        y: PAD + r * (NODE_H + ROW_GAP),
      }));
    }
    const w = PAD * 2 + cols.size * NODE_W + (cols.size - 1) * COL_GAP;
    const h = PAD * 2 + maxRows * NODE_H + (maxRows - 1) * ROW_GAP;

    const parts = [];
    for (const e of g.edges) {
      const a = pos.get(e.from), b = pos.get(e.to);
      const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
      const mx = (x1 + x2) / 2;
      parts.push(`<path d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" class="mm-edge" marker-end="url(#mm-arrow)"/>`);
      if (e.label) parts.push(`<text x="${mx}" y="${(y1 + y2) / 2 - 4}" class="mm-elabel">${esc(e.label)}</text>`);
    }
    for (const [id, n] of g.nodes) {
      const p = pos.get(id);
      const rows = n.label.split(/<br\s*\/?>/);
      const startY = p.y + NODE_H / 2 - ((rows.length - 1) * LINE_H) / 2 + 4;
      const text = rows.map((r, k) =>
        `<tspan x="${p.x + NODE_W / 2}" y="${startY + k * LINE_H}">${esc(r)}</tspan>`).join("");
      const title = n.zone ? `<title>${esc(n.zone)}</title>` : "";
      parts.push(`<g class="mm-node">${title}<rect x="${p.x}" y="${p.y}" width="${NODE_W}" height="${NODE_H}" rx="6"/><text class="mm-label">${text}</text></g>`);
    }
    const zones = [...new Set([...g.nodes.values()].map((n) => n.zone).filter(Boolean))];
    const caption = zones.length
      ? `<figcaption class="mm-cap">Zones: ${zones.map(esc).join(" · ")} (hover a box)</figcaption>` : "";
    return `<figure class="mermaid"><svg viewBox="0 0 ${w} ${h}" width="100%" role="img">`
      + `<defs><marker id="mm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">`
      + `<path d="M0 0 L10 5 L0 10 z"/></marker></defs>${parts.join("")}</svg>${caption}</figure>`;
  }

  function renderMarkdown(md) {
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0, para = [], inCode = false, code = [], lang = "";
    const flush = () => { if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; } };
    const closeFence = () => {
      const body = code.join("\n");
      let html = null;
      // A malformed diagram must never take the page down with it.
      if (lang === "mermaid") { try { html = mermaidSvg(body); } catch (_e) { html = null; } }
      out.push(html || "<pre><code>" + esc(body) + "</code></pre>");
      code = []; inCode = false; lang = "";
    };

    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        if (inCode) { closeFence(); }
        else { flush(); inCode = true; lang = line.replace(/^```/, "").trim().toLowerCase(); }
        i++; continue;
      }
      if (inCode) { code.push(line); i++; continue; }
      if (/^#{1,6}\s/.test(line)) {
        flush(); const m = line.match(/^(#{1,6})\s+(.*)$/);
        out.push(`<h${m[1].length}>` + inline(m[2]) + `</h${m[1].length}>`); i++; continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        flush(); const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push("<li>" + inline(lines[i].replace(/^\s*[-*]\s+/, "")) + "</li>"); i++; }
        out.push("<ul>" + items.join("") + "</ul>"); continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        flush(); const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push("<li>" + inline(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>"); i++; }
        out.push("<ol>" + items.join("") + "</ol>"); continue;
      }
      if (/^\s*\|/.test(line) && i + 1 < lines.length && /-/.test(lines[i + 1]) && /^\s*\|?[\s:|-]+\|/.test(lines[i + 1])) {
        flush(); const header = splitRow(line); i += 2; const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i++; }
        out.push("<table class='md'><thead><tr>" + header.map((h) => "<th>" + inline(h) + "</th>").join("") + "</tr></thead><tbody>"
          + rows.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") + "</tbody></table>");
        continue;
      }
      if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
      if (/^\s*$/.test(line)) { flush(); i++; continue; }
      para.push(line.trim()); i++;
    }
    if (inCode) closeFence(); // unterminated fence at EOF
    flush();
    return out.join("\n");
  }

  window.renderMarkdown = renderMarkdown;
})();
