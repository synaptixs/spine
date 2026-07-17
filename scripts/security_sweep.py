#!/usr/bin/env python3
"""Phase 1 of the security review — the broad net. See docs/specs/security-review-plan.md.

One Haiku 4.5 pass per file in src/, submitted via the Batch API (50% off, and this
is not latency-sensitive). Output is a CANDIDATE LIST, not findings: the prompt asks
for coverage, deliberately over-reporting, because filtering here would hide exactly
the uncertain-but-real items Phase 3's adversarial pass exists to adjudicate. Expect a
high false-positive rate. That is the design, not a defect.

Why no prompt caching: the shared system prefix is ~700 tokens and Haiku 4.5's minimum
cacheable prefix is 4096, so cache_control would silently no-op. The file bodies differ
per request anyway — there is no reusable prefix worth the write premium.

Why no `effort` / thinking: `effort` errors on Haiku 4.5, and triage doesn't need
extended thinking. Both would cost output tokens for no recall.

Usage:
    uv run --with anthropic python scripts/security_sweep.py --dry-run   # cost estimate
    uv run --with anthropic python scripts/security_sweep.py --submit    # send batch
    uv run --with anthropic python scripts/security_sweep.py --fetch ID  # collect
    uv run --with anthropic python scripts/security_sweep.py --live      # concurrent, no batch

--live runs the same 267 requests concurrently against the standard endpoint instead
of the Batch API. It costs 2x batch (no 50% discount) but returns in minutes — use it
when the batch queue stalls. Same prompt, schema, and output file, so results are
directly comparable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

LIVE_CONCURRENCY = 12  # well under Haiku 4.5's RPM; polite, not maximal

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
OUT = REPO / "docs" / "security-sweep-phase1.local.json"

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 8000

SYSTEM = """You are a security engineer triaging a Python codebase for a security review.

This codebase (Spine) is an agent orchestration platform. It clones untrusted remote
git repositories, runs LLM-generated code against them, shells out to build tools,
serves a FastAPI registry with a web UI, and executes MCP tools in an agentic loop.
Untrusted input therefore includes: repository URLs, cloned repository CONTENTS, LLM
output, and HTTP request bodies.

You are the FIRST of several passes. Your job is COVERAGE, not precision. Report every
issue you find, including ones you are uncertain about or consider low-severity. Do not
filter for importance or confidence — a later adversarial pass will try to refute each
one, and a finding you drop here is never recovered. It is better to surface something
that later gets filtered out than to silently drop a real bug.

Prioritise, but do not restrict yourself to:
  - SSRF and URL/host allow-list bypasses (redirects, DNS rebinding, userinfo-in-URL,
    IPv6 encodings, file:// and other schemes)
  - command injection, argument injection, and unsafe subprocess construction
  - path traversal, symlink following, unsafe archive/clone extraction
  - prompt injection reaching a tool-calling loop, or LLM output reaching a sink
  - authn/authz gaps, missing access control, IDOR
  - unsafe deserialization (pickle, yaml.load, eval, exec)
  - SQL injection and unparameterised query construction
  - XSS / HTML-or-markdown escaping bugs in rendering code
  - secret handling: logging, echoing, persisting, or embedding credentials
  - SSRF-adjacent: unbounded reads, zip bombs, resource exhaustion

Ignore pure style, typing, and performance issues. `assert` used to narrow a type for a
checker is not a finding. Shelling out via an argv list (never shell=True) is how this
project is designed to work — flag it only if the argv is built from untrusted input in
a way that permits argument injection.

For each candidate give the 1-indexed line, a short category slug, your honest severity
and confidence, a one-sentence summary, and the concrete path by which untrusted input
reaches the problem. If a file has nothing, return an empty list — do not invent."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "summary": {"type": "string"},
                    "reachability": {"type": "string"},
                },
                "required": ["line", "category", "severity", "confidence", "summary", "reachability"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def load_env() -> None:
    """Bridge .env the same way `orchestrator doctor` does, without adding a dependency."""
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


def targets() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.is_file())


def manifest(paths: list[Path]) -> dict[str, str]:
    # Index-based custom_ids: file paths here exceed the custom_id length budget, and
    # batch results arrive in ANY order — so the mapping has to be explicit.
    return {f"f{i:04d}": str(p.relative_to(REPO)) for i, p in enumerate(paths)}


def build(paths: list[Path], ids: dict[str, str]) -> list[dict[str, Any]]:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    rev = {v: k for k, v in ids.items()}
    reqs = []
    for p in paths:
        rel = str(p.relative_to(REPO))
        reqs.append(
            Request(
                custom_id=rev[rel],
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM,
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                    messages=[{"role": "user", "content": numbered_body(p)}],
                ),
            )
        )
    return reqs


def numbered_body(p: Path) -> str:
    rel = str(p.relative_to(REPO))
    body = p.read_text(encoding="utf-8", errors="replace")
    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(body.splitlines(), 1))
    return f"File: {rel}\n\n{numbered}"


def write_results(rows: list[dict[str, Any]], errors: list[str]) -> None:
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: (rank.get(r.get("severity", "low"), 9), r["file"], r.get("line", 0)))
    OUT.write_text(json.dumps({"candidates": rows, "errors": errors}, indent=2))
    print(f"\ncandidates: {len(rows)}   errors: {len(errors)}")
    for sev in ("critical", "high", "medium", "low"):
        n = sum(1 for r in rows if r.get("severity") == sev)
        if n:
            print(f"  {sev:8} {n}")
    print(f"\nwritten: {OUT}")


async def live(paths: list[Path]) -> None:
    import anthropic

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(LIVE_CONCURRENCY)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    done = 0

    async def one(p: Path) -> None:
        nonlocal done
        rel = str(p.relative_to(REPO))
        async with sem:
            try:
                msg = await client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM,
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                    messages=[{"role": "user", "content": numbered_body(p)}],
                )
            except Exception as exc:  # noqa: BLE001 — record and continue; one bad file != abort
                errors.append(f"{rel}: {type(exc).__name__}")
                return
            if msg.stop_reason == "refusal":
                errors.append(f"{rel}: refusal")
                return
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                errors.append(f"{rel}: unparseable output")
                return
            for c in payload.get("candidates", []):
                rows.append({"file": rel, **c})
        done += 1
        if done % 25 == 0 or done == len(paths):
            print(f"  {done}/{len(paths)} files")

    await asyncio.gather(*(one(p) for p in paths))
    write_results(rows, errors)


def dry_run(paths: list[Path]) -> None:
    total = sum(p.stat().st_size for p in paths)
    # ~3.3 chars/token for Python. Rough on purpose: this is a go/no-go sanity check,
    # not an invoice. count_tokens is free if you want the real number.
    inp = total / 3.3 + len(paths) * 750  # + system prompt per request
    out = len(paths) * 400  # triage replies are short; most files return []
    cost = (inp / 1e6 * 1.00 + out / 1e6 * 5.00) * 0.5  # Haiku 4.5, batch = 50% off
    print(f"files:            {len(paths)}")
    print(f"bytes:            {total:,}")
    print(f"est input tokens: {inp:,.0f}")
    print(f"est output tokens:{out:,.0f}")
    print(f"est cost (batch): ${cost:,.2f}")


def submit(paths: list[Path]) -> None:
    import anthropic

    ids = manifest(paths)
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=build(paths, ids))
    state = {"batch_id": batch.id, "manifest": ids}
    Path(OUT.with_suffix(".manifest.json")).write_text(json.dumps(state, indent=2))
    print(f"batch:    {batch.id}")
    print(f"status:   {batch.processing_status}")
    print(f"manifest: {OUT.with_suffix('.manifest.json')}")
    print(f"\nfetch with:\n  uv run --with anthropic python {Path(__file__).name} --fetch {batch.id}")


def fetch(batch_id: str) -> None:
    import anthropic

    client = anthropic.Anthropic()
    state = json.loads(Path(OUT.with_suffix(".manifest.json")).read_text())
    ids: dict[str, str] = state["manifest"]

    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        print(f"  {b.processing_status}: {b.request_counts.processing} processing…")
        time.sleep(30)

    print(f"succeeded={b.request_counts.succeeded} errored={b.request_counts.errored}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in client.messages.batches.results(batch_id):
        rel = ids.get(result.custom_id, result.custom_id)
        if result.result.type != "succeeded":
            errors.append(f"{rel}: {result.result.type}")
            continue
        msg = result.result.message
        if msg.stop_reason == "refusal":
            errors.append(f"{rel}: refusal")
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            errors.append(f"{rel}: unparseable output")
            continue
        for c in payload.get("candidates", []):
            rows.append({"file": rel, **c})

    write_results(rows, errors)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--submit", action="store_true")
    g.add_argument("--fetch", metavar="BATCH_ID")
    g.add_argument("--live", action="store_true")
    args = ap.parse_args()

    paths = targets()
    if args.dry_run:
        dry_run(paths)
        return 0

    load_env()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set and not found in .env", file=sys.stderr)
        return 1

    if args.submit:
        submit(paths)
    elif args.live:
        asyncio.run(live(paths))
    else:
        fetch(args.fetch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
