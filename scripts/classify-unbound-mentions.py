"""Why do unbound doc mentions fail to bind? Deterministic classification, no model.

Run when someone proposes closing the doc-binding gap:

    python scripts/classify-unbound-mentions.py

The headline "N% of `Doc` sections bind to nothing" invites the reading that N% of the
documentation failed to bind. Measured, most of it never made a claim about this codebase —
it is prose about TypeScript, GitHub and OpenSpec. This separates the two.

Populations, in the order a mention is tested (first match wins):

  external   the graph's own `external` nodes — httpx, react. Real code, not first-party,
             so there is nothing first-party to point at.
  builtin    a Python builtin or stdlib module name. `SyntaxError` will drift for ever.
  no-kind    appears in first-party source as a name the vocabulary has no node kind for:
             a parameter, a local, a module-level constant, an attribute, a keyword arg.
  absent     appears nowhere in first-party source. Prose, or a genuine drift claim.
"""

from __future__ import annotations

import ast
import builtins
import re
import sys
from collections import Counter
from pathlib import Path

from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.docs import DocReconciler, extract_mentions
from orchestrator.pkg.extractor import RepoCodeExtractor

ROOT = Path(".")
code = RepoCodeExtractor().extract(ROOT)
rec = DocReconciler(code, repo_root=ROOT)

external = {n.name.lower() for n in code.nodes if not n.grounded}
external |= {n.id.split(":", 1)[-1].lower() for n in code.nodes if not n.grounded}
stdlib = {m.lower() for m in sys.stdlib_module_names} | {b.lower() for b in dir(builtins)}

# Names that exist in first-party source but have no node kind.
no_kind: set[str] = set()
for py in sorted(ROOT.rglob("*.py")):
    if any(part.startswith(".") or part in {"node_modules", "__pycache__"} for part in py.parts):
        continue
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            no_kind.add(node.arg.lower())
        elif isinstance(node, ast.Name):
            no_kind.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            no_kind.add(node.attr.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            no_kind.add(node.arg.lower())

stems = {
    f.stem.lower()
    for f in ROOT.rglob("*")
    if f.is_file() and not any(pt.startswith(".") or pt == "node_modules" for pt in f.parts)
}

buckets: Counter[str] = Counter()
per_section: dict[str, set[str]] = {}
KINDS = ("not-a-claim", "external", "builtin", "no-kind", "file-stem", "absent")
samples: dict[str, list[str]] = {k: [] for k in KINDS}
HEX40 = re.compile(r"^[0-9a-f]{7,40}\$")


def not_a_claim(text: str, mention) -> bool:
    """Shapes that are not claims about this codebase, so they cannot be binding failures."""
    if not rec._can_drift(mention):
        return True  # the repo's own rule: CamelCase prose, ALL-CAPS, plain lowercase words
    return bool(" " in text or "/" in text or "=" in text or "://" in text or HEX40.match(text))


for page in read_doc_pages(ROOT):
    ms = extract_mentions(page)
    if not ms:
        continue
    binds = [(m, rec.bind(m, base_dir=page.base_dir)) for m in ms]
    if any(len(b.anchor_ids) == 1 for _m, b in binds):
        continue  # section already binds
    for m, b in binds:
        if b.anchor_ids or b.anchor_files:
            continue
        t = m.text.lower()
        leaf = t.rsplit(".", 1)[-1]
        if not_a_claim(m.text, m):
            k = "not-a-claim"
        elif t in external or leaf in external:
            k = "external"
        elif t in stdlib or leaf in stdlib:
            k = "builtin"
        elif t in no_kind or leaf in no_kind:
            k = "no-kind"
        elif leaf in stems:
            k = "file-stem"
        else:
            k = "absent"
        buckets[k] += 1
        per_section.setdefault(page.title, set()).add(k)
        if len(samples[k]) < 8 and m.text not in samples[k]:
            samples[k].append(m.text)

total = sum(buckets.values())
print(f"unbound mentions in sections that bind to nothing: {total:,}\n")
for k in KINDS:
    n = buckets[k]
    print(f"  {k:9s} {n:6,d}  {n / total * 100:5.1f}%   e.g. {', '.join(samples[k][:5])}")

cannot = {"not-a-claim", "external", "builtin", "no-kind", "file-stem"}
secs = len(per_section)
hopeless = sum(1 for kinds in per_section.values() if kinds <= cannot)
print(f"\n  sections examined                     : {secs:,}")
print(f"  whose every mention CANNOT bind        : {hopeless:,}  ({hopeless / secs * 100:.0f}%)")
print(f"  with at least one 'absent' mention     : {secs - hopeless:,}")
