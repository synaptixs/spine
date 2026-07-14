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

  function renderMarkdown(md) {
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0, para = [], inCode = false, code = [];
    const flush = () => { if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; } };

    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        if (inCode) { out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); code = []; inCode = false; }
        else { flush(); inCode = true; }
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
    if (inCode) out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>");
    flush();
    return out.join("\n");
  }

  window.renderMarkdown = renderMarkdown;
})();
