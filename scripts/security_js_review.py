#!/usr/bin/env python3
"""Phase 2 coverage-gap closer — the web/static JS surface. See docs/specs/security-review-plan.md.

The Phase 1 sweep globbed src/**/*.py and never saw the 22 hand-rolled JS files under
registry/api/web/static — the XSS surface (no build step, no framework auto-escaping,
the known md.js fence-escaping quirk). This is small (~2.4k lines) and XSS reachability
spans files (a render helper called from another), so it's reviewed as ONE whole-surface
Opus 4.8 pass rather than per-file: the model sees every sink and its callers at once.
Verdicts are adjudicated inline (confirmed/needs_context/refuted), so this is Phase 1 +
Phase 3 folded into one request for a surface that fits in a single context.

Usage:
    uv run --with anthropic python scripts/security_js_review.py --dry-run
    uv run --with anthropic python scripts/security_js_review.py --run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "src" / "orchestrator" / "registry" / "api" / "web" / "static"
OUT = REPO / "docs" / "security-js-review.local.json"

MODEL = "claude-opus-4-8"
MAX_TOKENS = 32000  # adaptive thinking shares this budget; stream so it isn't truncated

SYSTEM = """You are a senior application-security reviewer auditing the client-side JavaScript
of Spine's operator web UI for DOM-based XSS and related client-side issues. This UI has NO
build step and NO framework auto-escaping — it is vanilla JS that builds DOM from data.

Untrusted data reaching this code includes: repository file contents and names, knowledge
that `understand` derived from an untrusted cloned repo, markdown rendered client-side
(note the known quirk that md.js escapes fenced code but diagrams render raw), API
responses echoing any of the above, and URL/query parameters.

Look for: innerHTML/outerHTML/insertAdjacentHTML built from data, document.write, unsanitised
markdown→HTML, javascript:/data: URLs in href/src, DOM clobbering, eval/new Function, event-
handler injection, and template strings interpolated into HTML without escaping.

You are the only pass on this surface, so adjudicate each finding yourself using an
asymmetric rule: mark "confirmed" only when untrusted data reaches an HTML/script sink with
no escaping you can see; "refuted" when the value is escaped, textContent (not innerHTML),
a hardcoded literal, or otherwise safe; "needs_context" when the sink is real but whether
the data is untrusted depends on a caller not shown. Prefer needs_context over refuted when
unsure. Give a one-line rationale naming the sink and the data path for each."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["confirmed", "needs_context", "refuted"]},
                    "sink": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "rationale": {"type": "string"},
                },
                "required": ["file", "line", "verdict", "sink", "severity", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def load_env() -> None:
    if os.getenv("ANTHROPIC_API_KEY"):
        return
    env = REPO / ".env"
    if not env.is_file():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and line.split("=", 1)[0].strip() == "ANTHROPIC_API_KEY":
            os.environ.setdefault("ANTHROPIC_API_KEY", line.split("=", 1)[1].strip().strip("'\""))


def targets() -> list[Path]:
    return sorted(p for p in STATIC.rglob("*") if p.suffix in {".js", ".html", ".css"} and p.is_file())


def corpus(paths: list[Path]) -> str:
    blocks = []
    for p in paths:
        rel = p.relative_to(REPO)
        body = p.read_text(encoding="utf-8", errors="replace")
        numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(body.splitlines(), 1))
        blocks.append(f"===== FILE: {rel} =====\n{numbered}")
    return "\n\n".join(blocks)


def dry_run(paths: list[Path]) -> None:
    text = corpus(paths)
    inp = len(text) / 3.3 + 500
    print(f"files: {len(paths)}  bytes: {len(text):,}")
    print(f"est input tokens: {inp:,.0f}")
    print(f"est cost (live):  ${inp / 1e6 * 5.00 + MAX_TOKENS / 1e6 * 25.00:,.2f}")


def run(paths: list[Path]) -> None:
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Review this web UI JS surface:\n\n{corpus(paths)}"}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        print("refusal", file=sys.stderr)
        return
    if msg.stop_reason == "max_tokens":
        print(f"truncated at max_tokens={MAX_TOKENS} — raise it", file=sys.stderr)
        return
    text = next((b.text for b in msg.content if b.type == "text"), "")
    data = json.loads(text)
    rows = data.get("findings", [])
    order = {"confirmed": 0, "needs_context": 1, "refuted": 2}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), r["file"], r.get("line", 0)))
    OUT.write_text(json.dumps({"findings": rows}, indent=2))
    n = {"confirmed": 0, "needs_context": 0, "refuted": 0}
    for r in rows:
        n[r["verdict"]] = n.get(r["verdict"], 0) + 1
    print(f"findings: {len(rows)}  " + " ".join(f"{k}={v}" for k, v in n.items()))
    for r in rows:
        if r["verdict"] != "refuted":
            rat = r["rationale"][:80]
            print(f"  [{r['verdict']}/{r['severity']}] {r['file']}:{r['line']}  {r['sink']} — {rat}")
    print(f"\nwritten: {OUT}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--run", action="store_true")
    args = ap.parse_args()
    paths = targets()
    if args.dry_run:
        dry_run(paths)
        return 0
    load_env()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set and not found in .env", file=sys.stderr)
        return 1
    run(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
