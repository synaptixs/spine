#!/usr/bin/env python3
"""Phase 3 of the security review — adversarial verification. See docs/specs/security-review-plan.md.

Phase 1 (security_sweep.py) is coverage-first and over-reports by design: 863 candidates,
most of them false positives. This pass adjudicates them. Each critical/high candidate goes
to Opus 4.8 — a strong model, because a weak refuter rubber-stamps — with a window of the
actual code and a mandate to REFUTE.

The refutation rule is the crux, and it is asymmetric on purpose:
  - REFUTE only on positive evidence of safety shown in the code: the input is validated
    or sanitized here, the value is a hardcoded literal, the path is dead/unreachable, a
    gate or allow-list guards it, or the flagged behavior is the component's designed
    contract (e.g. a gateway routing validated inputs to a handler).
  - Do NOT refute merely because reachability can't be *proven* from the window. A real
    bug whose reachability lives in an unseen caller must survive as needs_context, not
    die. Killing on absence-of-proof would defeat the point of Phase 1's high recall.

So a candidate dies only when the code affirmatively shows it's safe. Everything else
survives to a human Phase 2 review — confirmed (code shows it's real) or needs_context
(consistent with the vuln, reachability depends on code not shown).

Single strong-model verifier, not a 3-vote majority: at this candidate count the asymmetric
"refute only on proof of safety" rule already protects recall, and one Opus pass is the
$5 the plan budgeted. Escalate survivors to multi-vote only if the confirmed set is still
noisy.

Usage:
    uv run --with anthropic python scripts/security_verify.py --dry-run
    uv run --with anthropic python scripts/security_verify.py --run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
IN = REPO / "docs" / "security-sweep-phase1.local.json"
OUT = REPO / "docs" / "security-verify-phase3.local.json"

MODEL = "claude-opus-4-8"
MAX_TOKENS = 2000
CONCURRENCY = 8  # Opus RPM is lower than Haiku; stay polite
WINDOW = 80  # lines of context on each side of the flagged line
SEVERITIES = {"critical", "high"}

SYSTEM = """You are a senior application-security reviewer adjudicating a candidate finding
produced by an earlier, deliberately over-reporting triage pass. Your default stance is
skeptical: most candidates you see are false positives, and your job is to REFUTE them.

This codebase (Spine) is an agent orchestration platform. It clones untrusted remote git
repositories, runs LLM-generated code against them, shells out to build tools, serves a
FastAPI registry, and executes MCP tools in an agentic loop. Untrusted input includes:
repository URLs, cloned repository CONTENTS, LLM output, and HTTP request bodies. Code that
is only ever driven by the operator's own trusted configuration is NOT an attack surface.

Decide the verdict using this ASYMMETRIC rule:

  - "refuted" — the code shown affirmatively demonstrates safety: the untrusted input is
    validated/sanitized/escaped before the sink, the flagged value is a hardcoded literal
    or operator-only config, the path is dead or unreachable, an allow-list or auth gate
    guards it, or the behavior is the component's intended contract. Refute when you can
    point to WHY it's safe.

  - "confirmed" — the code shown demonstrates a real, reachable vulnerability: untrusted
    input reaches a dangerous sink with no adequate guard, and you can trace the path.

  - "needs_context" — the window is consistent with the vulnerability but reachability
    depends on code not shown (e.g. who calls this function, whether the caller validates).
    Use this rather than refuting when you cannot prove safety. Do NOT refute merely
    because you can't prove danger from the window alone.

Refute only on positive evidence of safety. When genuinely unsure, prefer needs_context —
a wrongly-refuted real bug is worse here than a survivor a human later dismisses.

Give a one-sentence rationale naming the specific guard (for refuted), the specific
input→sink path (for confirmed), or the specific missing caller/validator (for needs_context)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirmed", "refuted", "needs_context"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "confidence", "rationale"],
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
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == "ANTHROPIC_API_KEY":
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def candidates() -> list[dict[str, Any]]:
    data = json.loads(IN.read_text())
    return [c for c in data["candidates"] if c.get("severity") in SEVERITIES]


def window(cand: dict[str, Any]) -> str:
    """Imports (for context on what's trusted) + a line-numbered window around the flag."""
    fp = REPO / cand["file"]
    if not fp.is_file():
        return "(file not found)"
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    line = int(cand.get("line", 1))
    lo, hi = max(1, line - WINDOW), min(len(lines), line + WINDOW)
    head = [f"{i}: {ln}" for i, ln in enumerate(lines[:40], 1) if ("import " in ln or "def " in ln)][:25]
    body = [f"{i}: {ln}" for i, ln in enumerate(lines[lo - 1 : hi], lo)]
    return (
        "# imports / signatures (head of file):\n"
        + "\n".join(head)
        + f"\n\n# code around line {line}:\n"
        + "\n".join(body)
    )


def prompt(cand: dict[str, Any]) -> str:
    return (
        f"File: {cand['file']}\n"
        f"Candidate finding at line {cand['line']}:\n"
        f"  category:    {cand.get('category')}\n"
        f"  severity:    {cand.get('severity')} (triage confidence: {cand.get('confidence')})\n"
        f"  summary:     {cand.get('summary')}\n"
        f"  claimed reachability: {cand.get('reachability')}\n\n"
        f"Code:\n{window(cand)}\n\n"
        f"Adjudicate this candidate."
    )


# --- --recheck: re-adjudicate the needs_context survivors WITH cross-file callers ---
OUT_RECHECK = REPO / "docs" / "security-verify-recheck.local.json"
RECHECK_WINDOW = 150


def needs_context() -> list[dict[str, Any]]:
    data = json.loads(OUT.read_text())
    return [r for r in data["verified"] if r.get("verify", {}).get("verdict") == "needs_context"]


def _enclosing_def(fp: Path, line: int) -> str | None:
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(min(line, len(lines)) - 1, -1, -1):
        m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[i])
        if m:
            return m.group(1)
    return None


def callers(cand: dict[str, Any]) -> str:
    """Grep the enclosing function's callers across src/ (the context that was missing)."""
    fp = REPO / cand["file"]
    if not fp.is_file():
        return "(file not found)"
    name = _enclosing_def(fp, int(cand.get("line", 1)))
    if not name:
        return "(no enclosing function found — the finding is at module scope)"
    try:
        res = subprocess.run(
            ["grep", "-rnE", rf"\b{re.escape(name)}\s*\(", str(REPO / "src")],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return "(caller grep failed)"
    hits = [ln for ln in res.stdout.splitlines() if f"def {name}" not in ln][:20]
    return f"# callers of `{name}(` across src/ ({len(hits)} shown):\n" + ("\n".join(hits) or "(none found)")


def recheck_prompt(cand: dict[str, Any]) -> str:
    fp = REPO / cand["file"]
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines() if fp.is_file() else []
    line = int(cand.get("line", 1))
    lo, hi = max(1, line - RECHECK_WINDOW), min(len(lines), line + RECHECK_WINDOW)
    body = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines[lo - 1 : hi], lo))
    return (
        f"File: {cand['file']}, finding at line {line}: {cand.get('category')} — {cand.get('summary')}\n"
        f"A prior pass marked this NEEDS_CONTEXT because reachability depended on callers not shown.\n"
        f"You now have the callers. Confirm only if untrusted input reaches the sink through a "
        f"caller that does NOT validate it; refute if any caller validates/constrains the input or "
        f"the input is trusted; stay needs_context only if the callers shown are still insufficient.\n\n"
        f"Code around the finding:\n{body}\n\n{callers(cand)}\n\nRe-adjudicate."
    )


def dry_run(cands: list[dict[str, Any]]) -> None:
    inp = sum(len(prompt(c)) for c in cands) / 3.3 + len(cands) * 400
    out = len(cands) * 250
    cost = inp / 1e6 * 5.00 + out / 1e6 * 25.00  # Opus 4.8, live (no batch)
    print(f"candidates (critical+high): {len(cands)}")
    print(f"est input tokens:  {inp:,.0f}")
    print(f"est output tokens: {out:,.0f}")
    print(f"est cost (live):   ${cost:,.2f}")


async def run(
    cands: list[dict[str, Any]],
    build: Any = prompt,
    out_path: Path = OUT,
    effort: str = "medium",
) -> None:
    import anthropic

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(CONCURRENCY)
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    done = 0

    async def one(c: dict[str, Any]) -> None:
        nonlocal done
        tag = f"{c['file']}:{c['line']}"
        async with sem:
            try:
                msg = await client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "adaptive"},
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                    system=SYSTEM,
                    messages=[{"role": "user", "content": build(c)}],
                )
            except Exception as exc:  # noqa: BLE001 — record and continue
                errors.append(f"{tag}: {type(exc).__name__}")
                return
            if msg.stop_reason == "refusal":
                errors.append(f"{tag}: refusal")
                return
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                v = json.loads(text)
            except json.JSONDecodeError:
                errors.append(f"{tag}: unparseable")
                return
            out.append({**c, "verify": v})
        done += 1
        if done % 25 == 0 or done == len(cands):
            print(f"  {done}/{len(cands)} verified")

    await asyncio.gather(*(one(c) for c in cands))

    order = {"confirmed": 0, "needs_context": 1, "refuted": 2}
    srank = {"critical": 0, "high": 1}
    out.sort(key=lambda r: (order.get(r["verify"]["verdict"], 9), srank.get(r["severity"], 9), r["file"]))
    out_path.write_text(json.dumps({"verified": out, "errors": errors}, indent=2))

    n = {"confirmed": 0, "needs_context": 0, "refuted": 0}
    for r in out:
        n[r["verify"]["verdict"]] = n.get(r["verify"]["verdict"], 0) + 1
    print(f"\nverified: {len(out)}   errors: {len(errors)}")
    print(f"  confirmed:     {n['confirmed']}")
    print(f"  needs_context: {n['needs_context']}")
    print(f"  refuted:       {n['refuted']}  (killed)")
    print(f"\nsurvivors (confirmed + needs_context): {n['confirmed'] + n['needs_context']}")
    print(f"written: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--recheck", action="store_true", help="re-adjudicate needs_context with callers")
    args = ap.parse_args()

    if args.recheck:
        if not OUT.is_file():
            print(f"Phase 3 output not found: {OUT}. Run --run first.", file=sys.stderr)
            return 1
        load_env()
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set and not found in .env", file=sys.stderr)
            return 1
        nc = needs_context()
        print(f"re-checking {len(nc)} needs_context findings with cross-file callers…")
        asyncio.run(run(nc, build=recheck_prompt, out_path=OUT_RECHECK, effort="high"))
        return 0

    if not IN.is_file():
        print(f"Phase 1 output not found: {IN}. Run security_sweep.py first.", file=sys.stderr)
        return 1
    cands = candidates()

    if args.dry_run:
        dry_run(cands)
        return 0

    load_env()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set and not found in .env", file=sys.stderr)
        return 1
    asyncio.run(run(cands))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
